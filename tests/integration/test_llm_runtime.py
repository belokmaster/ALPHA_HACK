"""Effective runtime survives process reconstruction and safe schema evolution."""

import importlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import BaseModel, SecretStr
from sqlalchemy import select, text

from app.budgets.service import BudgetLedger
from app.budgets.tables import model_calls, reservations
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.factory import load_tariffs
from app.llm.gateway import LLMGateway
from app.llm.runtime import RuntimeConfig, load_runtime
from app.llm.tables import attempts, key_gates, llm_runs
from contracts.v1 import ErrorCode, LLMRequest

pytestmark = pytest.mark.integration


class Reply(BaseModel):
    ok: bool


def mixed():
    raw = load_runtime().model_dump()
    for p, m in [("plan", "google/gemma-4-26b-a4b-it"), ("verify", "z-ai/glm-5.3-flash")]:
        raw["stages"][p]["model_id"] = m
    return RuntimeConfig.model_validate(raw)


def gateway(db, runtime, handler, *, key=None):
    return LLMGateway(
        BudgetLedger(db),
        runtime=runtime,
        config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True),
        api_key=SecretStr(key or uuid4().hex),
        tariffs=load_tariffs(Path("config/llm-tariffs.json")),
        transport=httpx.MockTransport(handler),
    )


def request(runtime, run, stage):
    s = runtime.stage(stage)
    return LLMRequest(
        run_id=run,
        call_id=run + "_" + stage,
        purpose=stage,
        model_id=s.model_id,
        max_output_tokens=s.max_output_tokens,
        prompt="fixture",
    )


async def test_per_stage_models_usage_reservation_and_durable_runtime(db):
    runtime = mixed()
    sent = []

    async def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        # Reservation and attempt must exist before HTTP, with this stage's tariff/model.
        async with db.connect() as c:
            row = (
                (
                    await c.execute(
                        select(attempts).where(attempts.c.run_id == run).order_by(attempts.c.id)
                    )
                )
                .mappings()
                .all()
            )
            active = next(r for r in row if r["state"] == "started")
            reservation = (
                (await c.execute(select(reservations).where(reservations.c.id == active["id"])))
                .mappings()
                .one()
            )
            tariff = g.tariffs[body["model"]]
            assert (
                active["model_id"] == body["model"] and active["output_bound"] == body["max_tokens"]
            )
            assert reservation["amount_rub"] == tariff.estimate(
                input_tokens=active["input_bound"], max_output_tokens=body["max_tokens"]
            )
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            },
        )

    g = gateway(db, runtime, handler)
    run = uuid4().hex
    await g.start_run(run)
    for p in ("plan", "draft", "verify"):
        result = await g.complete(request(runtime, run, p), Reply)
        assert result.usage.model_id == runtime.stage(p).model_id
        # The estimate multiplier increases the reserve, never fabricates provider spend.
        assert result.usage.cost_rub == g.tariffs[result.usage.model_id].quote(
            input_tokens=10, max_output_tokens=4
        )
    assert [r["model"] for r in sent] == [
        runtime.stage(p).model_id for p in ("plan", "draft", "verify")
    ]
    async with db.connect() as c:
        # model_calls stores individual model_id; attempts joins it by durable call ID.
        calls = (
            (
                await c.execute(
                    select(model_calls).where(
                        model_calls.c.id.in_([run + "_" + p for p in ("plan", "draft", "verify")])
                    )
                )
            )
            .mappings()
            .all()
        )
        assert {c["model_id"] for c in calls} == {s["model"] for s in sent}
        pin = await c.scalar(select(llm_runs.c.runtime_snapshot).where(llm_runs.c.id == run))
        assert pin == runtime.snapshot("adaptive")
    # A reconstructed client cannot change even a non-price runtime parameter on an active run.
    changed = runtime.model_dump()
    changed["version"] = "new-config"
    other = gateway(
        db, RuntimeConfig.model_validate(changed), handler, key=g._key.get_secret_value()
    )
    with pytest.raises(AppError) as err:
        await other.start_run(run)
    assert err.value.code == ErrorCode.data_conflict and len(sent) == 3


async def test_uncertain_record_uses_verify_model_not_primary(db):
    runtime = mixed()

    async def handler(req):
        body = json.loads(req.content)
        if body["model"] == runtime.stages.verify.model_id:
            raise httpx.ReadTimeout("controlled unknown")
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            },
        )

    g = gateway(db, runtime, handler)
    run = uuid4().hex
    await g.start_run(run)
    await g.complete(request(runtime, run, "draft"), Reply)
    with pytest.raises(AppError) as err:
        await g.complete(request(runtime, run, "verify"), Reply)
    assert err.value.code == ErrorCode.llm_outcome_unknown
    assert err.value.model_id == runtime.stages.verify.model_id
    async with db.connect() as c:
        call = (
            (await c.execute(select(model_calls).where(model_calls.c.id == run + "_verify")))
            .mappings()
            .one()
        )
        assert call["model_id"] == runtime.stages.verify.model_id and call["outcome"] == "uncertain"
        assert (
            await c.scalar(
                select(key_gates.c.active_call_id).where(key_gates.c.scope_id == g.scope)
            )
            == run + "_verify"
        )


async def test_runtime_migration_preserves_legacy_rows_and_gate(db):
    async with db.connect() as c:
        async with c.begin():
            # Session-local tables shadow production names on this isolated connection only.
            await c.execute(
                text(
                    "CREATE TEMP TABLE llm_runs (id text, models_used int, quoted_rub numeric) ON COMMIT DROP"
                )
            )
            await c.execute(text("INSERT INTO llm_runs VALUES ('legacy', 2, 0.42)"))
            await c.execute(
                text(
                    "CREATE TEMP TABLE llm_key_gates (scope_id text, active_call_id text) ON COMMIT DROP"
                )
            )
            await c.execute(text("INSERT INTO llm_key_gates VALUES ('key', 'uncertain-call')"))

            def upgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    importlib.import_module("migrations.versions.0005_llm_runtime").upgrade()

            await c.run_sync(upgrade)
            row = (await c.execute(text("SELECT * FROM llm_runs"))).mappings().one()
            assert dict(row) == {
                "id": "legacy",
                "models_used": 2,
                "quoted_rub": Decimal("0.42"),
                "runtime_snapshot": None,
            }
            assert (
                await c.scalar(text("SELECT active_call_id FROM llm_key_gates")) == "uncertain-call"
            )


async def test_configured_call_timeout_cancels_transport_and_holds_reserve(db):
    import asyncio

    raw = load_runtime().model_dump()
    raw["timeouts"]["transport_seconds"] = 0.01
    raw["timeouts"]["call_seconds"] = 0.02
    runtime = RuntimeConfig.model_validate(raw)
    sent = []

    async def never_finishes(req):
        sent.append(req)
        await asyncio.Event().wait()

    g = gateway(db, runtime, never_finishes)
    assert g.transport.timeout == 0.01
    run = uuid4().hex
    await g.start_run(run)
    with pytest.raises(AppError) as err:
        # A regression to the old hardcoded 12 s cannot pass this check.
        await asyncio.wait_for(g.complete(request(runtime, run, "plan"), Reply), 2)
    assert err.value.code == ErrorCode.llm_outcome_unknown and len(sent) == 1
    async with db.connect() as c:
        call = (
            (await c.execute(select(model_calls).where(model_calls.c.id == run + "_plan")))
            .mappings()
            .one()
        )
        assert call["outcome"] == "uncertain"
        assert (
            await c.scalar(
                select(key_gates.c.active_call_id).where(key_gates.c.scope_id == g.scope)
            )
            == run + "_plan"
        )
