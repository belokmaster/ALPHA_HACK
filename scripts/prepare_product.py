"""Publish extra source-backed events to the marked offline demo DB. No source edits/OCR/LLM."""

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.ingestion.pdf.pipeline import document_id, load_corpus_config
from contracts.v1 import Document, Event


async def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if (
        url.rsplit("/", 1)[-1] != "alfa_test"
        or os.environ.get("ANALYSIS_MODE") != "controlled"
        or os.environ.get("LLM_API_KEY")
    ):
        raise SystemExit("Explicit offline disposable test DB required")
    engine = create_async_engine(url)
    cfg = load_corpus_config()
    try:
        async with engine.begin() as c:
            marker = await c.scalar(
                text(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
                )
            )
            if marker != "g1-disposable-offline-scenarios-v1":
                raise ValueError("Disposable DB marker missing")
            for spec in json.loads(Path("config/product-events.json").read_text())["events"]:
                did = document_id(cfg["documents"][spec["filename"]], cfg)
                doc = Document.model_validate(
                    await c.scalar(select(t.documents.c.payload).where(t.documents.c.id == did))
                )
                eid = "ev_" + did + "_" + spec["span"]
                ev = await c.scalar(select(t.evidence.c.payload).where(t.evidence.c.id == eid))
                if not ev or ev["document_version_id"] != did or doc.published_at is None:
                    raise ValueError("Original versioned source/span/date missing")
                item = Event(
                    id=spec["id"],
                    title=spec["title"],
                    domain="case",
                    source_version_id=doc.source_version_id,
                    evidence_ids=[eid],
                    topics=spec["topics"],
                    event_at=doc.published_at,
                    available_at=None,
                    event_time_source="publication",
                )
                old = await c.scalar(select(t.events.c.payload).where(t.events.c.id == item.id))
                if old and old != item.model_dump(mode="json"):
                    raise ValueError("Immutable source-backed event collision")
                await c.execute(
                    insert(t.events)
                    .values(
                        id=item.id,
                        source_id=item.source_version_id,
                        payload=item.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
                print(
                    json.dumps(
                        {
                            "event": item.model_dump(mode="json"),
                            "value_kind": ev["value_kind"],
                            "quality": ev["quality"],
                            "note": "agent_page_checked is not human verification; no new OCR or available_at",
                        },
                        ensure_ascii=False,
                    )
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
