"""Free source check + exact imported text/row/cell coverage in an isolated test DB."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.ingestion.news.fetch import SafeFetcher
from app.ingestion.news.parse import FEEDS, Tree, article, normalize, rss
from app.ingestion.news.service import sync_source
from app.ingestion.news.tables import items, versions


async def check(source_name):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    publisher, _, feed_url = FEEDS[source_name]
    fetcher = SafeFetcher()
    feed = await fetcher.fetch(feed_url, kind="rss")
    item = rss(feed.body, feed_url)[0]
    snapshot = await fetcher.fetch(item.url, kind="html")
    observed = datetime.now(UTC)
    parsed = article(snapshot, item, observed)
    tree = Tree()
    # These sources currently declare UTF-8; parser rejects unknown encoding.
    tree.feed(snapshot.body.decode("utf-8"))

    def source_text(node):
        if not hasattr(node, "children"):
            return node
        if node.tag in {"script", "style", "iframe", "object", "svg"}:
            return ""
        return " ".join(source_text(child) for child in node.children)

    cells = [normalize(source_text(n)) for n in tree.nodes if n.tag in ("td", "th")]
    meaningful = [x for x in cells if x]
    missing = [x for x in meaningful if x not in parsed.text]
    # For table-bearing source pages all cells must survive, including symbols for
    # unavailable observations and explanatory footnotes. No numeric extraction.
    assert not missing, {"missing_table_cells": missing[:10]}
    db = create_async_engine(url)
    try:

        class CapturedFetch:
            async def fetch(self, url, *, kind):
                if kind == "rss" and url == feed_url:
                    return feed
                if kind == "html" and url == item.url:
                    return snapshot
                raise ValueError("Unexpected source URL")

        first = await sync_source(
            db, source_name, fetcher=CapturedFetch(), clock=lambda: observed, limit=1
        )
        repeat = await sync_source(
            db, source_name, fetcher=CapturedFetch(), clock=lambda: observed, limit=1
        )
        assert first.status == repeat.status == "ok"
        assert repeat.imported == 0 and repeat.unchanged == 1
        async with db.connect() as c:
            row = (
                (
                    await c.execute(
                        select(versions)
                        .join(items, items.c.id == versions.c.item_id)
                        .where(items.c.publisher == publisher, items.c.canonical_url == parsed.url)
                        .order_by(versions.c.sequence.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one()
            )
            doc = (
                await c.execute(
                    select(t.documents.c.payload).where(t.documents.c.id == row["document_id"])
                )
            ).scalar_one()
            evs = (
                (
                    await c.execute(
                        select(t.evidence.c.payload)
                        .where(t.evidence.c.document_id == row["document_id"])
                        .order_by(t.evidence.c.id)
                    )
                )
                .scalars()
                .all()
            )
            evs = sorted(evs, key=lambda e: int(e["span"].split(":")[1]))
            assert "".join(e["quote"] for e in evs) == doc["text"] == parsed.text
        report = {
            "source": source_name,
            "observed_at": observed.isoformat(),
            "url": parsed.url,
            "rss_url": item.url,
            "guid": item.guid,
            "title": parsed.title,
            "published_at": parsed.published_at.isoformat(),
            "date_precision": parsed.precision,
            "html_sha256": hashlib.sha256(snapshot.body).hexdigest(),
            "rss_sha256": hashlib.sha256(feed.body).hexdigest(),
            "text_sha256": hashlib.sha256(parsed.text.encode()).hexdigest(),
            "characters": len(parsed.text),
            "table_cells": len(cells),
            "nonempty_cells": len(meaningful),
            "missing_cells": len(missing),
            "table_rows": parsed.text.count("[строка]"),
            "first": first.model_dump(mode="json"),
            "repeat": repeat.model_dump(mode="json"),
            "document_id": doc["id"],
            "evidence_count": len(evs),
            "review": "agent_check_only; table structure retained, no extracted verified metrics",
        }
        out = Path("artifacts/integration/news")
        out.mkdir(parents=True, exist_ok=True)
        (out / (source_name + ".xml")).write_bytes(feed.body)
        (out / (source_name + ".html")).write_bytes(snapshot.body)
        (out / (source_name + "-text.txt")).write_text(parsed.text)
        (out / (source_name + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await db.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["cbr-press", "rosstat"], required=True)
    asyncio.run(check(parser.parse_args().source))


if __name__ == "__main__":
    main()
