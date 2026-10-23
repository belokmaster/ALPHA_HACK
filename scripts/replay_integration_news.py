"""Replay hash-pinned real B02 captures into the disposable integration DB, without HTTP.

Requires artifacts/integration/news/{source}.{json,xml,html} copied from the G1
review materials. This is source capture replay, not fabricated UI/LLM fixtures.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.ingestion.news.fetch import Snapshot
from app.ingestion.news.parse import FEEDS
from app.ingestion.news.service import sync_source


async def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Disposable alfa_test TEST_DATABASE_URL required")
    db = create_async_engine(url)
    try:
        async with db.connect() as c:
            marker = await c.scalar(
                text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
                )
            )
            if marker != "g1-disposable-offline-scenarios-v1":
                raise SystemExit("Fresh disposable scenario DB marker required")
        for source in ("cbr-press", "rosstat"):
            root = Path("artifacts/integration/news")
            report = json.loads((root / f"{source}.json").read_text())
            xml, html = [(root / f"{source}.{ext}").read_bytes() for ext in ("xml", "html")]
            for body, key in ((xml, "rss_sha256"), (html, "html_sha256")):
                if hashlib.sha256(body).hexdigest() != report[key]:
                    raise ValueError("Original capture hash mismatch")
            feed_url = FEEDS[source][2]

            class CapturedFetch:
                async def fetch(self, target, *, kind):
                    if target == feed_url and kind == "rss":
                        return Snapshot(target, xml, "application/rss+xml")
                    if target in (report["url"], report["rss_url"]) and kind == "html":
                        return Snapshot(report["url"], html, "text/html; charset=utf-8")
                    raise ValueError("Capture does not contain requested URL")

            state = await sync_source(
                db,
                source,
                fetcher=CapturedFetch(),
                clock=lambda: datetime.fromisoformat(report["observed_at"]),
                limit=1,
            )
            if state.status != "ok":
                raise ValueError(state)
            print(
                json.dumps(
                    {
                        "source": source,
                        "capture_replay": True,
                        "state": state.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
