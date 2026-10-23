"""Test-only worker: exercise live policy with the real controlled gateway.

Never uses a provider transport/key. Refuses unmarked or non-controlled databases.
Not a live entrypoint; invoke only on the disposable scenario Compose project.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from app.core.config import Settings
from app.core.db import engine
from app.jobs import worker
from app.llm.factory import create_gateway


async def main():
    cfg = Settings()
    if (
        cfg.analysis_mode != "controlled"
        or cfg.live_llm_enabled
        or cfg.llm_api_key.get_secret_value()
        or not cfg.demo_mode
        or engine.url.database != "alfa_test"
    ):
        raise RuntimeError("Test worker requires controlled, no live/key, alfa_test")
    async with engine.connect() as c:
        marker = await c.scalar(
            text(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
            )
        )
    if marker != "g1-disposable-offline-scenarios-v1":
        raise RuntimeError("Disposable database marker missing")
    # Only replace the transport construction boundary. The actual gateway,
    # ledger, queue, authorization, retrieval and persistence remain production code.
    with (
        patch("app.jobs.analysis.settings", cfg.model_copy(update={"analysis_mode": "gateway"})),
        patch(
            "app.jobs.analysis.create_gateway",
            side_effect=lambda db, settings, **kw: create_gateway(db, cfg, **kw),
        ),
    ):
        await worker.main()


if __name__ == "__main__":
    asyncio.run(main())
