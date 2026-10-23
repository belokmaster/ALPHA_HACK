"""Free network ingestion probe. Requires an explicit isolated /alfa_test database."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.ingestion.news.parse import FEEDS
from app.ingestion.news.service import sync_source
from app.ingestion.news.tables import items, versions
from contracts.v1 import Document, Event, Evidence, SourceVersion


async def smoke(args):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    engine = create_async_engine(url)
    try:
        first = await sync_source(engine, args.source, limit=1)
        report = {"source": args.source, "first": first.model_dump(mode="json")}
        if first.status != "ok":
            print(json.dumps(report, ensure_ascii=False))
            raise SystemExit(1)
        publisher = FEEDS[args.source][0]
        async with engine.connect() as c:
            row = (
                (
                    await c.execute(
                        select(versions)
                        .join(items, items.c.id == versions.c.item_id)
                        .where(items.c.publisher == publisher)
                        .order_by(versions.c.fetched_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one()
            )
            document = Document.model_validate(
                (
                    await c.execute(
                        select(t.documents.c.payload).where(t.documents.c.id == row["document_id"])
                    )
                ).scalar_one()
            )
            event = Event.model_validate(
                (
                    await c.execute(
                        select(t.events.c.payload).where(t.events.c.id == row["event_id"])
                    )
                ).scalar_one()
            )
            source = SourceVersion.model_validate(
                (
                    await c.execute(
                        select(t.source_versions.c.payload).where(
                            t.source_versions.c.id == row["source_id"]
                        )
                    )
                ).scalar_one()
            )
            evidence = [
                Evidence.model_validate(x)
                for x in (
                    await c.execute(
                        select(t.evidence.c.payload).where(t.evidence.c.document_id == document.id)
                    )
                ).scalars()
            ]
        assert document.domain == event.domain == source.domain == "live"
        assert evidence and event.evidence_ids == [e.id for e in evidence]
        assert "".join(e.quote for e in evidence) == document.text
        second = await sync_source(engine, args.source, limit=1)
        async with engine.connect() as c:
            count = (await c.execute(select(func.count()).select_from(versions))).scalar_one()
        report.update(
            repeat=second.model_dump(mode="json"),
            document_id=document.id,
            event_id=event.id,
            source_version_id=source.id,
            url=document.url,
            title=document.title,
            published_at=document.published_at.isoformat(),
            available_at=document.available_at.isoformat(),
            characters=len(document.text),
            evidence_count=len(evidence),
            stored_versions=count,
            source_sha256=source.ref.sha256,
            text_sha256=hashlib.sha256(document.text.encode()).hexdigest(),
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        snapshot = {
            "source": source.model_dump(mode="json"),
            "document": document.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in evidence],
        }
        output.with_name(output.stem + "-snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=list(FEEDS), default="cbr-press")
    parser.add_argument("--output", default="artifacts/b02/import-smoke.json")
    asyncio.run(smoke(parser.parse_args()))


if __name__ == "__main__":
    main()
