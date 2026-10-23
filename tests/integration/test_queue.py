from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update

from app.core import tables as t
from app.core.db import EXPECTED_DB_REVISION
from app.jobs import worker
from app.seed import seed

pytestmark = pytest.mark.integration


async def test_seed_migration_idempotent(db):
    async with db.connect() as c:
        before = (await c.execute(select(func.count()).select_from(t.companies))).scalar_one()
        assert (
            await c.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == EXPECTED_DB_REVISION
    await seed()
    await seed()
    async with db.connect() as c:
        assert (
            (await c.execute(select(func.count()).select_from(t.companies))).scalar_one()
            == before
            == 4
        )


async def test_bounded_recovery_and_fencing(db, monkeypatch):
    monkeypatch.setattr(worker, "engine", db)
    jid = str(uuid4())
    async with db.begin() as c:
        await c.execute(
            insert(t.jobs).values(
                id=jid,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="recovery",
                idempotency_key=jid,
                payload_hash="0" * 64,
                status="queued",
                created_at=datetime.now(UTC),
                payload={},
            )
        )
    first = await worker.claim()
    assert first["id"] == jid and first["attempt"] == 1
    async with db.begin() as c:
        await c.execute(
            update(t.jobs)
            .where(t.jobs.c.id == jid)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    second = await worker.claim()
    assert (
        second["id"] == jid
        and second["attempt"] == 2
        and first["lease_token"] != second["lease_token"]
    )
    async with db.begin() as c:
        await c.execute(
            update(t.jobs)
            .where(t.jobs.c.id == jid)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert await worker.claim() is None
    async with db.connect() as c:
        assert (
            await c.execute(select(t.jobs.c.status).where(t.jobs.c.id == jid))
        ).scalar_one() == "failed"


async def test_unknown_model_outcome_is_not_retried(db, monkeypatch):
    from decimal import Decimal

    from app.budgets.service import BudgetLedger, Tariff
    from app.budgets.tables import reservations

    monkeypatch.setattr(worker, "engine", db)
    jid = str(uuid4())
    rid = str(uuid4())
    async with db.begin() as c:
        await c.execute(
            insert(t.jobs).values(
                id=jid,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="unknown",
                idempotency_key=jid,
                payload_hash="0" * 64,
                status="running",
                attempt=1,
                lease_token="dead",
                lease_until=datetime.now(UTC) - timedelta(seconds=1),
                created_at=datetime.now(UTC),
                payload={},
            )
        )
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
    assert await worker.claim() is None
    async with db.connect() as c:
        job = (await c.execute(select(t.jobs).where(t.jobs.c.id == jid))).mappings().one()
        assert job["status"] == "failed" and job["attempt"] == 1
        assert (
            await c.execute(select(reservations.c.status).where(reservations.c.id == rid))
        ).scalar_one() == "reserved"


async def test_concurrent_claims_and_heartbeat(db, monkeypatch):
    import asyncio

    monkeypatch.setattr(worker, "engine", db)
    monkeypatch.setattr(worker.settings, "heartbeat_seconds", 1)
    ids = [str(uuid4()) for _ in range(2)]
    async with db.begin() as c:
        for jid in ids:
            await c.execute(
                insert(t.jobs).values(
                    id=jid,
                    principal_id="alice",
                    company_id="demo_a",
                    kind="analysis",
                    route="claim",
                    idempotency_key=jid,
                    payload_hash="0" * 64,
                    status="queued",
                    created_at=datetime.now(UTC),
                    payload={},
                )
            )
    first, second = await asyncio.gather(worker.claim(), worker.claim())
    assert {first["id"], second["id"]} == set(ids)
    task = asyncio.create_task(worker.heartbeat(first))
    await asyncio.sleep(1.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with db.begin() as c:
        renewed = (
            await c.execute(select(t.jobs.c.lease_until).where(t.jobs.c.id == first["id"]))
        ).scalar_one()
        assert renewed > first["lease_until"]
        await c.execute(update(t.jobs).where(t.jobs.c.id.in_(ids)).values(status="cancelled"))


async def test_typed_graph_does_not_create_principals_or_grants(db):
    async with db.connect() as c:
        assert set((await c.execute(select(t.principals.c.id))).scalars()) == {"alice", "bob"}
        assert set((await c.execute(select(t.relations.c.kind))).scalars()) == {
            "shared_person",
            "group",
            "self_transfer",
            "observed_payment",
        }
        assert (
            await c.execute(
                select(t.grants).where(
                    t.grants.c.principal_id == "alice", t.grants.c.company_id == "demo_hidden"
                )
            )
        ).first() is None
        issue = (
            await c.execute(
                select(t.companies.c.payload).where(t.companies.c.company_id == "conflict_u")
            )
        ).scalar_one()
        assert issue["status"] == "quarantined"
        payment = (
            await c.execute(
                select(t.relations.c.payload).where(t.relations.c.kind == "observed_payment")
            )
        ).scalar_one()
        assert payment["economic_role"] == "unknown" and payment["nodes"][-1] == "unknown_u"
