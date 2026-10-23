"""Read-only snapshot for manual review. No source import, generation or ACL changes."""

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from contracts.v1 import Analysis


async def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    root = Path("artifacts/integration")
    output = root / "g1-review-snapshot.json"
    if output.exists():
        raise SystemExit("Frozen snapshot exists; never overwrite review inputs")
    parent = Analysis.model_validate_json((root / "g1-analysis.json").read_bytes())
    follow = Analysis.model_validate_json((root / "g1-followup.json").read_bytes())
    assert follow.parent_analysis_id == parent.id
    news = [
        json.loads((root / "news" / (key + ".json")).read_text())
        for key in ("cbr-press", "rosstat")
    ]
    doc_ids = sorted(
        {e.document_version_id for a in (parent, follow) for e in a.evidence}
        | {n["document_id"] for n in news}
    )
    engine = create_async_engine(url)
    try:
        async with engine.connect() as c:
            transaction = await c.begin()
            await c.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            documents = list(
                (
                    await c.execute(
                        select(t.documents.c.payload).where(t.documents.c.id.in_(doc_ids))
                    )
                ).scalars()
            )
            assert len(documents) == len(doc_ids)
            source_ids = sorted(
                {d["source_version_id"] for d in documents}
                | {sid for f in parent.features for sid in f.depends_on_source_versions}
            )
            sources = list(
                (
                    await c.execute(
                        select(t.source_versions.c.payload).where(
                            t.source_versions.c.id.in_(source_ids)
                        )
                    )
                ).scalars()
            )
            evidence = list(
                (
                    await c.execute(
                        select(t.evidence.c.payload).where(t.evidence.c.document_id.in_(doc_ids))
                    )
                ).scalars()
            )
            saved = list(
                (
                    await c.execute(
                        select(t.analyses.c.payload).where(
                            t.analyses.c.id.in_([parent.id, follow.id])
                        )
                    )
                ).scalars()
            )
            for original in (parent, follow):
                actual = next(a for a in saved if a["id"] == original.id)
                # Saved API replay only changes delivery metadata, never substantive data.
                assert {k: v for k, v in actual.items() if k != "delivery"} == {
                    k: v for k, v in original.model_dump(mode="json").items() if k != "delivery"
                }
            event = (
                await c.execute(select(t.events.c.payload).where(t.events.c.id == parent.event_id))
            ).scalar_one()
            messages = [
                dict(row)
                for row in (
                    await c.execute(select(t.messages).where(t.messages.c.job_id == follow.job_id))
                ).mappings()
            ]
            principal_acl = (
                await c.execute(
                    select(t.principals.c.acl_version).where(
                        t.principals.c.id == parent.context.principal_id
                    )
                )
            ).scalar_one()
            await transaction.rollback()
        payload = {
            "mode": "read_only_snapshot",
            "analysis_ids": [parent.id, follow.id],
            "documents": sorted(documents, key=lambda d: d["id"]),
            "sources": sorted(sources, key=lambda s: s["id"]),
            "evidence": sorted(evidence, key=lambda e: e["id"]),
            "stored_analyses": sorted(saved, key=lambda a: a["id"]),
            "event": event,
            "messages": messages,
            "access_observation": {
                "pinned_acl_version": parent.context.acl_version,
                "current_acl_version": principal_acl,
                "note": "ACL test changed version; do not bypass rights to reopen old result. Use immutable offline snapshot.",
            },
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "file": str(output),
                    "documents": len(documents),
                    "source_versions": len(sources),
                    "analysis_ids": payload["analysis_ids"],
                    "access": payload["access_observation"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
