import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.core.config import Settings
from app.ingestion.news.parse import FEEDS
from app.ingestion.news.service import source_states, sync_source


async def run(args):
    engine = create_async_engine(Settings().database_url)
    try:
        if args.command == "update":
            keys = list(FEEDS) if args.source == "all" else [args.source]
            states = [await sync_source(engine, key, limit=args.limit) for key in keys]
            print(json.dumps([s.model_dump(mode="json") for s in states], ensure_ascii=False))
            if any(s.status in ("partial", "error") for s in states):
                raise SystemExit(1)
        elif args.command == "status":
            async with engine.connect() as c:
                states = await source_states(c)
            print(json.dumps([s.model_dump(mode="json") for s in states], ensure_ascii=False))
        else:
            async with engine.connect() as c:
                row = (
                    await c.execute(
                        select(t.documents.c.payload).where(t.documents.c.id == args.document_id)
                    )
                ).scalar_one()
            print(json.dumps(row, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Explicit free RSS/article refresh; no scheduler or LLM"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    update = sub.add_parser("update")
    update.add_argument("--source", choices=[*FEEDS, "all"], default="cbr-press")
    update.add_argument("--limit", type=int, choices=range(1, 51), default=5)
    sub.add_parser("status")
    show = sub.add_parser("show")
    show.add_argument("document_id")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
