"""Actual PostgreSQL + common gateway + managed HTTP. No provider calls."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from app.budgets.service import BudgetLedger, Tariff
from app.budgets.tables import model_calls, reservations
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.g1_live import LiveG1Client, authorize_live_g1
from app.llm.gateway import LLMGateway
from app.llm.tables import key_gates, llm_runs
from contracts.v1 import ErrorCode, LLMRequest

MODEL = "deepseek/deepseek-v4.1-flash"

pytestmark = pytest.mark.integration


class Reply(BaseModel):
    answer: int


def request(job, purpose):
    return LLMRequest(
        run_id=job + "_run",
        call_id=job + "_" + purpose,
        purpose=purpose,
        model_id=MODEL,
        prompt="Controlled policy fixture",
        max_output_tokens=900 if purpose == "draft" else 700,
    )


@pytest.mark.parametrize("mode", ["success", "timeout", "missing_usage", "invalid_json"])
async def test_two_calls_maximum_and_uncertain_never_verifies(db, mode):
    sent = []

    def handle(request):
        sent.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("controlled unknown outcome")
        body = {
            "model": MODEL,
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": '{"answer":42}'}}],
        }
        if mode == "missing_usage":
            body.pop("usage")
        elif mode == "invalid_json":
            body["choices"][0]["message"]["content"] = "invalid"
        return httpx.Response(200, json=body)

    gateway = LLMGateway(
        BudgetLedger(db),
        config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True),
        api_key=SecretStr("offline_g1_policy_" + uuid4().hex),
        tariffs={MODEL: Tariff(MODEL, "offline-1", Decimal("1"), Decimal("1"), True)},
        mode="two_call_smoke",
        transport=httpx.MockTransport(handle),
    )
    job = "policy_" + uuid4().hex  # No job FK needed: this test starts a standalone run.
    auth = authorize_live_g1(gateway, Decimal("5"))
    client = LiveG1Client(gateway, job, auth)
    # The wrapper requires the exact job identity; use an actual persisted synthetic job.
    from datetime import UTC, datetime

    from app.core import tables as t

    async with db.begin() as c:
        await c.execute(
            t.jobs.insert().values(
                id=job,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="test/g1-policy",
                idempotency_key=job,
                payload_hash="0" * 64,
                status="succeeded",
                created_at=datetime.now(UTC),
                payload={},
            )
        )
    await client.start_run(job + "_run", job_id=job)
    if mode == "success":
        await client.complete(request(job, "draft"), Reply)
        await client.complete(request(job, "verify"), Reply)
        expected = 2
    else:
        with pytest.raises(AppError):
            await client.complete(request(job, "draft"), Reply)
        with pytest.raises(AppError):
            await client.complete(request(job, "verify"), Reply)
        expected = 1
    with pytest.raises(AppError):
        await client.complete(request(job, "draft"), Reply)
    assert len(sent) == expected
    # A reconstructed worker client cannot use a fresh ID or replay draft into HTTP.
    restarted = LiveG1Client(gateway, job, auth)
    with pytest.raises(AppError) as error:
        await restarted.complete(request(job, "draft"), Reply)
    assert error.value.code in {ErrorCode.idempotency_conflict, ErrorCode.llm_busy}
    assert len(sent) == expected
    async with db.connect() as c:
        calls = (
            (await c.execute(select(model_calls).where(model_calls.c.job_id == job)))
            .mappings()
            .all()
        )
        assert len(calls) == expected
        run = (
            (await c.execute(select(llm_runs).where(llm_runs.c.id == job + "_run")))
            .mappings()
            .one()
        )
        assert run["quoted_rub"] <= auth.maximum_reserve_rub <= auth.budget_rub <= 5
        gate = await c.scalar(
            select(key_gates.c.active_call_id).where(key_gates.c.scope_id == gateway.scope)
        )
        if mode in {"timeout", "missing_usage"}:
            assert calls[0]["outcome"] == "uncertain" and gate == job + "_draft"
            status = await c.scalar(
                select(reservations.c.status).where(reservations.c.id == job + "_draft")
            )
            assert status == "uncertain"
        else:
            assert gate is None
