"""Read-only evidence of ledger/job invariants in the explicitly isolated test DB."""

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.llm.tables import attempts, key_gates, llm_runs


async def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    db = create_async_engine(url)
    try:
        async with db.connect() as c:
            analyses = list((await c.execute(select(t.analyses.c.payload))).scalars())
            runs = list((await c.execute(select(t.runs.c.payload))).scalars())
            result = {
                "alembic_heads": list(
                    (await c.execute(text("select version_num from alembic_version"))).scalars()
                ),
                "bank_vectors": (
                    await c.execute(
                        text("select count(*) from evidence where embedding is not null")
                    )
                ).scalar_one(),
                "runs": runs,
                "analyses": [
                    {
                        "id": a["id"],
                        "event": a["event_id"],
                        "delivery": a["delivery"],
                        "usage_status": a["usage_status"],
                        "claims": len(a["claims"]),
                        "bank_evidence_status": a["bank_evidence_status"],
                        "stop_notices": [
                            n["text"] for n in a["notices"] if "остановлена:" in n["text"]
                        ],
                    }
                    for a in analyses
                ],
                "llm_runs": [dict(x) for x in (await c.execute(select(llm_runs))).mappings()],
                "attempts": [dict(x) for x in (await c.execute(select(attempts))).mappings()],
                "gates": [dict(x) for x in (await c.execute(select(key_gates))).mappings()],
                "reservations": [
                    dict(x)
                    for x in (await c.execute(select(t.metadata.tables["reservations"]))).mappings()
                ],
                "model_calls": [
                    dict(x)
                    for x in (await c.execute(select(t.metadata.tables["model_calls"]))).mappings()
                ],
                "paid_network_calls": 0,
                "mode": "controlled/offline",
            }
        out = Path("artifacts/integration/state.json")
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        print(
            json.dumps(
                {
                    "analyses": len(analyses),
                    "runs": len(runs),
                    "model_calls": len(result["model_calls"]),
                    "uncertain": sum(x["outcome"] == "uncertain" for x in result["model_calls"]),
                    "alembic_heads": result["alembic_heads"],
                    "bank_vectors": result["bank_vectors"],
                }
            )
        )
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
