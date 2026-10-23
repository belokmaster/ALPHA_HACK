"""Real PostgreSQL, fake account/model HTTP; never uses a provider key."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from app.budgets.service import BudgetLedger, budget_day_at
from app.budgets.tables import model_calls, reservations
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.gateway import LLMGateway
from app.llm.session import SessionBudget
from app.llm.tables import external_expenses, key_gates
from contracts.v1 import LLMRequest
from tests.integration.test_llm_gateway import MODEL, envelope, tariff

pytestmark = pytest.mark.integration


class Reply(BaseModel):
    answer: int


async def setup(
    db,
    monkeypatch,
    *,
    max_calls=2,
    budget="50",
    content='{"answer":42}',
    reason="stop",
    timeout=False,
    refusal=None,
    missing_usage=False,
):
    external = [Decimal("0.0064385253075")]
    sent = []

    async def observe(*args):
        return external[0]

    monkeypatch.setattr("app.llm.session.account_spend", observe)

    async def handler(req):
        sent.append(json.loads(req.content))
        if timeout:
            raise httpx.ReadTimeout("offline timeout")
        external[0] += Decimal("0.003")
        response = envelope(content)
        response["choices"][0]["finish_reason"] = reason
        response["choices"][0]["message"]["refusal"] = refusal
        response["usage"]["completion_tokens_details"] = {"reasoning_tokens": 7}
        if missing_usage:
            del response["usage"]
        return httpx.Response(200, json=response)

    g = LLMGateway(
        BudgetLedger(db),
        config=LLMConfig(
            live_enabled=True,
            billable_output_cap_confirmed=False,
            live_session_id=uuid4().hex,
        ),
        api_key=SecretStr("offline_" + uuid4().hex),
        tariffs={MODEL: tariff()},
        transport=httpx.MockTransport(handler),
    )
    await g.session.open(
        expected_spend=external[0],
        max_calls=max_calls,
        budget_rub=Decimal(budget),
        authorization_id="offline-approval-" + g.config.live_session_id,
    )
    return g, external, sent


async def call(g):
    run = uuid4().hex
    await g.start_run(run)
    req = LLMRequest(
        run_id=run,
        call_id=run + "_draft",
        purpose="draft",
        model_id=MODEL,
        prompt="offline",
        max_output_tokens=100,
    )
    return await g.complete(req, Reply)


async def test_session_persists_across_gateway_reconstruction_and_caps_all_jobs(db, monkeypatch):
    g, external, sent = await setup(db, monkeypatch)
    result = await call(g)
    assert result.value.answer == 42 and result.usage.cost_rub == Decimal("0.000120")
    fresh = LLMGateway(
        BudgetLedger(db),
        config=g.config,
        api_key=g._key,
        tariffs=g.tariffs,
        transport=g.transport.transport,
    )
    await call(fresh)
    with pytest.raises(AppError):
        await call(fresh)
    assert len(sent) == 2
    row = await fresh.session.status()
    assert row["calls_sent"] == row["calls_allocated"] == 2
    assert row["reserved_rub"] == 0 and row["spent_rub"] == Decimal("0.006")
    assert row["last_spend"] - row["baseline_spend"] == Decimal("0.006")
    async with db.connect() as c:
        adjustments = (
            (
                await c.execute(
                    select(external_expenses.c.amount_rub).where(
                        external_expenses.c.scope_id == g.scope
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sum(adjustments) == 2 * Decimal("0.002880")
    await fresh.session.stop(close=True)
    # Even explicitly repeating open cannot reset or reopen a closed permission.
    with pytest.raises(AppError):
        await fresh.session.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal("50"))
    with pytest.raises(AppError):
        await call(g)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": '{"answer":4', "reason": "length"},
        {"content": ""},
        {"content": "not json"},
        {"content": '{"answer":42}', "refusal": "cannot comply"},
    ],
)
async def test_known_usage_settles_even_on_invalid_output(db, monkeypatch, kwargs):
    g, _, sent = await setup(db, monkeypatch, **kwargs)
    with pytest.raises(AppError) as exc:
        await call(g)
    assert exc.value.code.value == "llm_invalid_response"
    row = await g.session.status()
    assert row["calls_sent"] == 1 and row["spent_rub"] == Decimal("0.003")
    assert row["reserved_rub"] == 0 and row["state"] == "open"
    assert len(sent) == 1


@pytest.mark.parametrize("kwargs", [{"timeout": True}, {"missing_usage": True}])
async def test_uncertain_holds_one_reserve_and_stops_all_future_jobs(db, monkeypatch, kwargs):
    g, _, sent = await setup(db, monkeypatch, **kwargs)
    with pytest.raises(AppError):
        await call(g)
    row = await g.session.status()
    assert row["calls_sent"] == 1 and row["reserved_rub"] == Decimal("1")
    assert row["spent_rub"] == 0 and row["state"] == "blocked"
    async with db.connect() as c:
        assert await c.scalar(
            select(key_gates.c.active_call_id).where(key_gates.c.scope_id == g.scope)
        )
        assert (
            await c.execute(select(reservations.c.status).where(reservations.c.scope_id == g.scope))
        ).scalar_one() == "uncertain"
    with pytest.raises(AppError):
        await call(g)
    assert len(sent) == 1


async def test_unattributed_expense_stops_before_dispatch_and_releases_unused_hold(db, monkeypatch):
    g, external, sent = await setup(db, monkeypatch)
    external[0] += Decimal("1")
    with pytest.raises(AppError):
        await call(g)
    row = await g.session.status()
    assert row["calls_sent"] == row["calls_allocated"] == 0
    assert row["reserved_rub"] == 0 and row["state"] == "blocked" and not sent
    async with db.connect() as c:
        assert not (
            await c.execute(
                select(model_calls.c.id)
                .join(reservations)
                .where(reservations.c.scope_id == g.scope)
            )
        ).first()


async def test_session_does_not_resume_on_day_reset_and_id_cannot_bypass_day(db, monkeypatch):
    g, external, sent = await setup(db, monkeypatch)
    another = SessionBudget(g.ledger, g.scope, uuid4().hex, g.config.base_url, g._key)

    with pytest.raises(AppError):
        await another.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal("50"))
    tomorrow = g.ledger.clock() + timedelta(days=1)
    g.ledger.clock = lambda: tomorrow
    with pytest.raises(AppError):
        await call(g)
    assert not sent
    with pytest.raises(AppError):
        await another.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal("50"))


async def test_budget_headroom_stops_before_session_limit(db, monkeypatch):
    g, _, sent = await setup(db, monkeypatch, budget="6")
    await call(g)
    with pytest.raises(AppError):
        await call(g)
    assert len(sent) == 1


async def test_daily_external_expense_is_counted_without_fake_model_calls(db, monkeypatch):
    from sqlalchemy import insert

    g, _, sent = await setup(db, monkeypatch)
    async with db.begin() as c:
        await c.execute(
            insert(external_expenses).values(
                id=uuid4().hex,
                scope_id=g.scope,
                budget_day=budget_day_at(g.ledger.clock()),
                amount_rub=Decimal("449.5"),
                reason="offline external daily cost",
            )
        )
    with pytest.raises(AppError) as exc:
        await call(g)
    assert exc.value.code.value == "budget_exceeded" and not sent
    row = await g.session.status()
    assert row["reserved_rub"] == 0 and row["state"] == "blocked"


async def test_external_settlement_replay_never_charges_twice(db, monkeypatch):
    g, _, _ = await setup(db, monkeypatch)
    result = await call(g)
    before = await g.session.status()
    await g.session.finish(result.call_id, result.usage.cost_rub)
    assert await g.session.status() == before


@pytest.mark.parametrize("rounding", [Decimal("0"), Decimal("1e-16"), Decimal("-1e-16")])
async def test_delayed_external_metadata_does_not_repeat_model(db, monkeypatch, rounding):
    g, external, sent = await setup(db, monkeypatch)
    observations = []

    async def delayed(*args):
        observations.append(external[0])
        # Before-send and first post-response observation still show baseline.
        return Decimal("0.0064385253075") + rounding if len(observations) <= 2 else external[0]

    monkeypatch.setattr("app.llm.session.account_spend", delayed)
    assert (await call(g)).value.answer == 42
    assert len(sent) == 1 and len(observations) == 3
    assert (await g.session.status())["reserved_rub"] == 0


async def test_cost_pending_stops_but_preserves_known_usage_then_explicit_resume(db, monkeypatch):
    g, external, sent = await setup(db, monkeypatch, max_calls=3)
    monkeypatch.setattr("app.llm.session.SPEND_POLL_INTERVAL", 0.01)
    monkeypatch.setattr("app.llm.session.SPEND_POLL_ATTEMPTS", 4)

    async def stale(*args):
        return Decimal("0.0064385253075")

    monkeypatch.setattr("app.llm.session.account_spend", stale)
    with pytest.raises(AppError) as exc:
        await call(g)
    assert exc.value.code.value == "backend_unavailable"
    row = await g.session.status()
    assert row["state"] == "blocked" and row["reserved_rub"] == 1 and row["spent_rub"] == 0
    with pytest.raises(AppError):
        await g.session.resume(Decimal("0.0064385253075"))
    async with db.connect() as c:
        record = (
            (
                await c.execute(
                    select(model_calls).join(reservations).where(reservations.c.scope_id == g.scope)
                )
            )
            .mappings()
            .one()
        )
    assert record["outcome"] == "known" and record["usage_status"] == "provider"

    async def updated(*args):
        return external[0]

    monkeypatch.setattr("app.llm.session.account_spend", updated)
    await g.reconcile(record["id"], input_tokens=100, output_tokens=10)
    await g.session.resume(external[0])
    row = await g.session.status()
    assert (
        row["calls_sent"] == 1 and row["spent_rub"] == Decimal(".003") and row["reserved_rub"] == 0
    )
    await call(g)
    assert len(sent) == 2
    await g.session.stop(close=True)
    with pytest.raises(AppError):
        await g.session.resume(external[0])


async def test_session_counter_is_atomic_between_processes(db, monkeypatch):
    g, _, _ = await setup(db, monkeypatch)
    code = """
import asyncio,sys
from decimal import Decimal
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from app.budgets.service import BudgetLedger
from app.llm.session import SessionBudget
from app.llm.tables import key_gates
from app.core.errors import AppError
async def main():
 e=create_async_engine(sys.argv[1]); s=SessionBudget(BudgetLedger(e),sys.argv[2],sys.argv[3],sys.argv[4],SecretStr('offline'))
 try:
  async with e.begin() as c:
   await c.execute(select(key_gates).where(key_gates.c.scope_id==s.scope).with_for_update())
   await s.allocate(c,Decimal('.01'))
 except AppError: return 2
 finally: await e.dispose()
 return 0
sys.exit(asyncio.run(main()))
"""

    async def process():
        p = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            os.environ["TEST_DATABASE_URL"],
            g.scope,
            g.config.live_session_id,
            g.config.base_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await p.communicate()
        assert p.returncode in (0, 2), err.decode()
        return p.returncode

    results = await asyncio.gather(*(process() for _ in range(6)))
    assert results.count(0) == 2 and results.count(2) == 4
    assert (await g.session.status())["calls_allocated"] == 2


async def test_session_migration_keeps_legacy_unknown_calls_and_gates(db):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    async with db.connect() as c:
        transaction = await c.begin()
        try:
            schema = "session_migration_" + uuid4().hex
            await c.execute(text("CREATE SCHEMA " + schema))
            await c.execute(text("SET LOCAL search_path TO " + schema + ",public"))
            await c.execute(text("CREATE TABLE llm_runs(id text, runtime_snapshot jsonb)"))
            await c.execute(text("INSERT INTO llm_runs VALUES ('legacy', NULL)"))
            await c.execute(text("CREATE TABLE llm_attempts(id text, state text)"))
            await c.execute(text("INSERT INTO llm_attempts VALUES ('pending', 'uncertain')"))
            await c.execute(text("CREATE TABLE llm_key_gates(scope_id text,active_call_id text)"))
            await c.execute(text("INSERT INTO llm_key_gates VALUES ('key','pending')"))

            def upgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    importlib.import_module("migrations.versions.0006_live_session").upgrade()

            await c.run_sync(upgrade)
            assert await c.scalar(text("SELECT active_call_id FROM llm_key_gates")) == "pending"
            assert await c.scalar(text("SELECT state FROM llm_attempts")) == "uncertain"
            assert await c.scalar(text("SELECT session_id FROM llm_attempts")) is None
            assert await c.scalar(text("SELECT runtime_snapshot FROM llm_runs")) is None
            assert await c.scalar(text("SELECT count(*) FROM llm_sessions")) == 0
        finally:
            await transaction.rollback()


async def test_new_explicit_authorization_after_close_preserves_old_counters(db, monkeypatch):
    g, external, sent = await setup(db, monkeypatch)
    await call(g)
    await g.session.stop(close=True)
    before = await g.session.status()
    authorization = before['authorization_id']
    another = SessionBudget(g.ledger, g.scope, uuid4().hex, g.config.base_url, g._key)
    # A changed session ID, even on the next day, cannot reuse the same approval.
    with pytest.raises(AppError):
        await another.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal('30'), authorization_id=authorization)
    tomorrow = g.ledger.clock() + timedelta(days=1)
    g.ledger.clock = lambda: tomorrow
    with pytest.raises(AppError):
        await another.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal('30'), authorization_id=authorization)
    await another.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal('30'), authorization_id='new-offline-approval-'+uuid4().hex)
    await another.validate()
    assert await g.session.status() == before
    assert (await another.status())['calls_sent'] == 0 and len(sent) == 1
    with pytest.raises(AppError):
        await g.session.validate()


async def test_concurrent_new_approvals_allow_only_one_active_session(db, monkeypatch):
    g, external, _ = await setup(db, monkeypatch)
    await g.session.stop(close=True)
    sessions_to_open = [SessionBudget(g.ledger, g.scope, uuid4().hex, g.config.base_url, g._key) for _ in range(2)]
    async def open_one(session):
        try:
            await session.open(expected_spend=external[0], max_calls=2, budget_rub=Decimal('30'), authorization_id='explicit-'+uuid4().hex)
            return True
        except AppError:
            return False
    assert sorted(await asyncio.gather(*(open_one(s) for s in sessions_to_open))) == [False, True]


@pytest.mark.parametrize("history_rows", [1, 2])
async def test_authorization_migration_preserves_legacy_rows_and_refuses_lossy_downgrade(db, history_rows):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    from migrations.schema_0006_sessions import sessions as legacy_sessions

    async with db.connect() as c:
        tx = await c.begin()
        try:
            schema = 'approval_migration_' + uuid4().hex
            await c.execute(text('CREATE SCHEMA ' + schema))
            await c.execute(text('SET LOCAL search_path TO ' + schema + ',public'))
            await c.run_sync(lambda conn: legacy_sessions.create(conn))
            await c.execute(text("""INSERT INTO llm_sessions
              (id,scope_id,budget_day,base_url,state,max_calls,budget_rub,spent_rub,reserved_rub,
               baseline_spend,last_spend,risk_acceptance,created_at)
              VALUES ('legacy','offline',CURRENT_DATE,'https://api.alfahack-gateway26.ru/v1',
              'closed',30,50,1.099579,0,0.0064385253075,1.10600500492482,'old risk',now())"""))
            before = await c.scalar(text('SELECT to_jsonb(s) FROM llm_sessions s'))
            migration = importlib.import_module('migrations.versions.0007_session_authorization')
            def upgrade(conn):
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
            await c.run_sync(upgrade)
            after = await c.scalar(text('SELECT to_jsonb(s) FROM llm_sessions s'))
            assert after.pop('authorization_id') == 'legacy:legacy'
            assert after == before
            # A later approval does not overwrite the closed historic authorization.
            await c.execute(text("""INSERT INTO llm_sessions
              (id,scope_id,budget_day,base_url,state,max_calls,budget_rub,
               baseline_spend,last_spend,risk_acceptance,authorization_id,created_at)
              VALUES ('new','offline',CURRENT_DATE,'https://api.alfahack-gateway26.ru/v1',
              'open',2,30,1.10600500492482,1.10600500492482,'old risk','new explicit approval',now())"""))
            if history_rows == 1:
                await c.execute(text("DELETE FROM llm_sessions WHERE id='legacy'"))
            savepoint = await c.begin_nested()
            def downgrade(conn):
                with Operations.context(MigrationContext.configure(conn)):
                    migration.downgrade()
            from sqlalchemy.exc import DBAPIError
            with pytest.raises(DBAPIError, match='Authorization history'):
                await c.run_sync(downgrade)
            await savepoint.rollback()
            assert await c.scalar(text('SELECT count(*) FROM llm_sessions')) == history_rows
        finally:
            await tx.rollback()
