import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update

from app.core import tables as t

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_idle_worker_exits_cleanly_on_signal(signum):
    code = """
import asyncio
from app.jobs import worker
async def ready():
    print('ready', flush=True)
    return None
worker.claim = ready
asyncio.run(worker.main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
        process.send_signal(signum)
        assert await asyncio.wait_for(process.wait(), 3) == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_active_worker_cancels_safely_and_releases_lease(db):
    jid = str(uuid4())
    async with db.begin() as conn:
        await conn.execute(
            insert(t.jobs).values(
                id=jid,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="shutdown",
                idempotency_key=jid,
                payload_hash="0" * 64,
                status="queued",
                created_at=datetime.now(UTC),
                payload={},
            )
        )
    code = """
import asyncio
from app.jobs import worker
async def waiting(conn, job):
    print('active', flush=True)
    await asyncio.sleep(3600)
worker.run_fixture = waiting
asyncio.run(worker.main(shutdown_grace_seconds=0.1))
"""
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, env=env, stdout=asyncio.subprocess.PIPE
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"active\n"
        process.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(process.wait(), 3) == 0
        async with db.begin() as conn:
            row = (await conn.execute(select(t.jobs).where(t.jobs.c.id == jid))).mappings().one()
            assert row["status"] == "queued" and row["attempt"] == 1
            assert row["lease_token"] is None and row["lease_until"] is None
            await conn.execute(update(t.jobs).where(t.jobs.c.id == jid).values(status="cancelled"))
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("state", ["exhausted", "uncertain", "fence_changed"])
async def test_shutdown_keeps_retry_limits_fencing_and_uncertain_reserve(db, monkeypatch, state):
    from decimal import Decimal

    from app.budgets.service import BudgetLedger, Tariff
    from app.budgets.tables import reservations
    from app.jobs import worker

    monkeypatch.setattr(worker, "engine", db)
    jid, rid = str(uuid4()), str(uuid4())
    attempt = worker.settings.max_attempts if state == "exhausted" else 1
    async with db.begin() as conn:
        await conn.execute(
            insert(t.jobs).values(
                id=jid,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="shutdown-guards",
                idempotency_key=jid,
                payload_hash="0" * 64,
                status="running",
                attempt=attempt,
                lease_token="current",
                created_at=datetime.now(UTC),
                payload={},
            )
        )
    if state == "uncertain":
        ledger = BudgetLedger(db)
        tariff = Tariff(
            model_id="deepseek/deepseek-v4.1-flash",
            version="fake",
            verified=True,
            input_rub_per_million=Decimal("1"),
            output_rub_per_million=Decimal("1"),
        )
        await ledger.reserve(rid, amount_rub=Decimal("10"), tariff=tariff, scope_id=jid)
        await ledger.mark_started(rid, call_id=str(uuid4()), job_id=jid)
    await worker.release_interrupted(
        {
            "id": jid,
            "attempt": attempt,
            "lease_token": "stale" if state == "fence_changed" else "current",
        }
    )
    async with db.begin() as conn:
        row = (await conn.execute(select(t.jobs).where(t.jobs.c.id == jid))).mappings().one()
        assert row["status"] == ("running" if state == "fence_changed" else "failed")
        if state == "uncertain":
            assert (
                await conn.execute(select(reservations.c.status).where(reservations.c.id == rid))
            ).scalar_one() == "reserved"
        await conn.execute(update(t.jobs).where(t.jobs.c.id == jid).values(status="cancelled"))
