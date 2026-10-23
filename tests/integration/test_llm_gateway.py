import asyncio
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select, update

from app.budgets.service import BudgetLedger, Tariff
from app.budgets.tables import budget_days, model_calls, reservations
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.gateway import GatewayError, LLMGateway
from app.llm.tables import attempts, key_gates, llm_runs
from contracts.v1 import ErrorCode, LLMRequest

pytestmark = pytest.mark.integration
MODEL = "deepseek/deepseek-v4.1-flash"


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int


def tariff():
    return Tariff(MODEL, "offline-test", Decimal("1"), Decimal("2"), True)


def envelope(content='{"answer":42}', **overrides):
    return {
        "model": MODEL,
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        **overrides,
    }


async def make_gateway(db, handler, *, key=None, prices=True, enabled=True):
    gateway = LLMGateway(
        BudgetLedger(db),
        config=LLMConfig(live_enabled=enabled, billable_output_cap_confirmed=True),
        api_key=SecretStr(key or "offline_" + uuid4().hex),
        tariffs={MODEL: tariff()} if prices else {},
        transport=httpx.MockTransport(handler),
    )
    run = uuid4().hex
    if enabled:
        await gateway.start_run(run)
    return gateway, run


def request(run, **kwargs):
    return LLMRequest(
        run_id=run,
        call_id=uuid4().hex,
        model_id=MODEL,
        prompt="Offline test",
        max_output_tokens=100,
        **kwargs,
    )


async def record(db, call_id):
    async with db.connect() as c:
        row = (
            (await c.execute(select(reservations).where(reservations.c.id == call_id)))
            .mappings()
            .one()
        )
        call = (
            (await c.execute(select(model_calls).where(model_calls.c.id == call_id)))
            .mappings()
            .one()
        )
    return row, call


async def test_success_reserves_before_http_without_holding_sql_locks(db):
    gateway = None
    req = None

    async def handler(http_request):
        assert str(http_request.url) == "https://changellenge.up.railway.app/v1/chat/completions"
        payload = json.loads(http_request.content)
        assert payload["model"] == MODEL and payload["max_tokens"] == 100
        assert not payload["stream"] and "response_format" not in payload
        async with db.begin() as c:
            # Both locks are available while the provider is running.
            day = (
                (
                    await c.execute(
                        select(budget_days)
                        .where(budget_days.c.scope_id == gateway.scope)
                        .with_for_update(nowait=True)
                    )
                )
                .mappings()
                .one()
            )
            gate = (
                (
                    await c.execute(
                        select(key_gates)
                        .where(key_gates.c.scope_id == gateway.scope)
                        .with_for_update(nowait=True)
                    )
                )
                .mappings()
                .one()
            )
            assert day["reserved_rub"] > 0 and gate["active_call_id"] == req.call_id
        row, call = await record(db, req.call_id)
        assert row["status"] == "reserved" and call["outcome"] == "started"
        return httpx.Response(200, json=envelope())

    gateway, run = await make_gateway(db, handler)
    req = request(run)
    result = await gateway.complete(req, Reply)
    assert result.value.answer == 42 and result.usage.cost_rub == Decimal("0.000120")
    row, call = await record(db, req.call_id)
    assert row["status"] == "settled" and call["usage_status"] == "provider"
    async with db.connect() as c:
        assert (
            await c.execute(
                select(key_gates.c.active_call_id).where(key_gates.c.scope_id == gateway.scope)
            )
        ).scalar_one() is None
    with pytest.raises(GatewayError) as err:
        await gateway.complete(req, Reply)
    assert err.value.code == ErrorCode.idempotency_conflict


@pytest.mark.parametrize("content", ["not json", '{"answer":"42"}', '{"answer":42,"extra":1}'])
async def test_invalid_output_is_settled_without_hidden_repair(db, content):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=envelope(content))

    gateway, run = await make_gateway(db, handler)
    req = request(run)
    with pytest.raises(GatewayError) as err:
        await gateway.complete(req, Reply)
    assert err.value.code == ErrorCode.llm_invalid_response
    assert err.value.usage.usage_status == "provider" and not err.value.retryable
    assert len(calls) == 1
    row, _ = await record(db, req.call_id)
    assert row["status"] == "settled"


@pytest.mark.parametrize(
    "mode", ["bad_envelope", "missing_usage", "429", "timeout", "model_mismatch", "usage_type"]
)
async def test_unknown_outcome_retains_reserve_and_blocks_key(db, mode):
    calls = []

    def handler(req):
        calls.append(req)
        if mode == "timeout":
            raise httpx.ReadTimeout("offline timeout")
        if mode == "bad_envelope":
            return httpx.Response(200, content=b"not json")
        if mode == "429":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        data = envelope()
        if mode == "missing_usage":
            data.pop("usage")
        if mode == "model_mismatch":
            data["model"] = "wrong/model"
        if mode == "usage_type":
            data["usage"]["prompt_tokens"] = True
        return httpx.Response(200, json=data)

    gateway, run = await make_gateway(db, handler)
    req = request(run)
    with pytest.raises(GatewayError) as err:
        await gateway.complete(req, Reply)
    expected = ErrorCode.llm_rate_limited if mode == "429" else ErrorCode.llm_outcome_unknown
    assert err.value.code == expected and len(calls) == 1
    row, call = await record(db, req.call_id)
    assert row["status"] == "uncertain" and call["outcome"] == "uncertain"
    assert row["actual_cost_rub"] is None
    # A new gateway object and run cannot bypass a key's durable blocked gate.
    other = LLMGateway(
        BudgetLedger(db),
        config=gateway.config,
        api_key=gateway._key,
        tariffs=gateway.tariffs,
        transport=httpx.MockTransport(handler),
    )
    new_run = uuid4().hex
    await other.start_run(new_run)
    with pytest.raises(GatewayError) as busy:
        await other.complete(request(new_run), Reply)
    assert busy.value.code == ErrorCode.llm_busy and len(calls) == 1
    # Only explicitly confirmed provider usage reconciles and unblocks the key.
    usage = await gateway.reconcile(req.call_id, input_tokens=100, output_tokens=10)
    assert usage.cost_rub == Decimal("0.000120")
    row, _ = await record(db, req.call_id)
    assert row["status"] == "settled"


async def test_confirmed_429_usage_is_known_without_retry(db):
    gateway, run = await make_gateway(db, lambda _: httpx.Response(429, json=envelope()))
    req = request(run)
    with pytest.raises(GatewayError) as err:
        await gateway.complete(req, Reply)
    assert err.value.code == ErrorCode.llm_rate_limited
    assert err.value.usage.cost_rub == Decimal("0.000120")
    assert (await record(db, req.call_id))[0]["status"] == "settled"


async def test_cancellation_during_http_retains_uncertain(db):
    entered = asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    gateway, run = await make_gateway(db, handler)
    req = request(run)
    task = asyncio.create_task(gateway.complete(req, Reply))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row, _ = await record(db, req.call_id)
    assert row["status"] == "uncertain"


async def test_run_deadline_cancels_actual_slow_transport(db):
    async def handler(_):
        await asyncio.sleep(10)
        return httpx.Response(200, json=envelope())

    gateway, run = await make_gateway(db, handler)
    async with db.begin() as c:
        await c.execute(
            update(llm_runs)
            .where(llm_runs.c.id == run)
            .values(deadline_at=gateway.ledger.clock() + timedelta(seconds=0.15))
        )
    req = request(run)
    with pytest.raises(GatewayError) as err:
        await asyncio.wait_for(gateway.complete(req, Reply), 2)
    assert err.value.code == ErrorCode.llm_outcome_unknown
    assert (await record(db, req.call_id))[0]["status"] == "uncertain"


@pytest.mark.parametrize("kind", ["disabled", "no_tariff", "daily_budget", "run_cost", "input_cap"])
async def test_preflight_gates_never_send_http(db, kind):
    calls = []
    gateway, run = await make_gateway(
        db, lambda req: calls.append(req), enabled=kind != "disabled", prices=kind != "no_tariff"
    )
    if kind == "daily_budget":
        await gateway.ledger.reserve(
            uuid4().hex, amount_rub=Decimal("450"), tariff=tariff(), scope_id=gateway.scope
        )
    if kind == "run_cost":
        gateway.tariffs[MODEL] = Tariff(
            MODEL, "offline-expensive", Decimal("10000"), Decimal("10000"), True
        )
    req = request(run)
    if kind == "input_cap":
        req.prompt = "x" * 8000
    with pytest.raises(AppError):
        await gateway.complete(req, Reply)
    assert not calls
    if kind == "daily_budget":
        async with db.connect() as c:
            assert (
                await c.execute(
                    select(key_gates.c.active_call_id).where(key_gates.c.scope_id == gateway.scope)
                )
            ).scalar_one() is None


async def test_run_counters_persist_and_bound_repair_fallback_and_tokens(db):
    gateway, run = await make_gateway(db, lambda _: httpx.Response(200, json=envelope()))
    for purpose in ("fallback", "retry", "repair"):
        with pytest.raises(GatewayError) as err:
            await gateway.complete(request(run, purpose=purpose), Reply)
        assert err.value.code == ErrorCode.budget_exceeded
    for _ in range(3):
        await gateway.complete(request(run), Reply)
    await gateway.start_run(run)  # Does not reset counters/deadline/config.
    with pytest.raises(GatewayError):
        await gateway.complete(request(run), Reply)
    async with db.connect() as c:
        row = (await c.execute(select(llm_runs).where(llm_runs.c.id == run))).mappings().one()
    assert row["models_used"] == 3 and row["repair_used"] == row["retry_used"] == 0


async def test_usage_overrun_is_recorded_and_gate_stays_closed(db):
    data = envelope(usage={"prompt_tokens": 100, "completion_tokens": 101})
    gateway, run = await make_gateway(db, lambda _: httpx.Response(200, json=data))
    req = request(run)
    with pytest.raises(GatewayError) as err:
        await gateway.complete(req, Reply)
    assert err.value.code == ErrorCode.budget_exceeded
    row, call = await record(db, req.call_id)
    assert row["status"] == "settled" and call["output_tokens"] == 101
    with pytest.raises(GatewayError) as busy:
        await gateway.complete(request(run), Reply)
    assert busy.value.code == ErrorCode.llm_busy


async def test_key_gate_survives_process_death_and_has_no_lease_expiry(db):
    # Real subprocess starts a call with an indefinitely waiting fake provider.
    key, run = "offline_" + uuid4().hex, uuid4().hex
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "tests/support/llm_process.py",
        key,
        run,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": "."},
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b"transport-entered\n"
        calls = []
        gateway, other_run = await make_gateway(db, lambda req: calls.append(req), key=key)
        with pytest.raises(GatewayError) as err:
            await gateway.complete(request(other_run), Reply)
        assert err.value.code == ErrorCode.llm_busy and not calls
        process.kill()
        await process.wait()
        with pytest.raises(GatewayError) as err:
            await gateway.complete(request(other_run), Reply)
        assert err.value.code == ErrorCode.llm_busy and not calls
        async with db.connect() as c:
            row = (
                (await c.execute(select(attempts).where(attempts.c.run_id == run))).mappings().one()
            )
        assert (await record(db, row["id"]))[1]["outcome"] == "started"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("limit", ["input", "output", "deadline"])
async def test_run_remaining_limits_are_shared_across_new_clients(db, limit):
    calls = []
    gateway, run = await make_gateway(db, lambda req: calls.append(req))
    async with db.begin() as c:
        changes = (
            {"input_used": 24000}
            if limit == "input"
            else {"output_used": 4000}
            if limit == "output"
            else {"deadline_at": gateway.ledger.clock() - timedelta(seconds=1)}
        )
        await c.execute(update(llm_runs).where(llm_runs.c.id == run).values(**changes))
    other = LLMGateway(
        BudgetLedger(db),
        config=gateway.config,
        api_key=gateway._key,
        tariffs=gateway.tariffs,
        transport=httpx.MockTransport(lambda req: calls.append(req)),
    )
    await other.start_run(run)
    with pytest.raises(GatewayError) as err:
        await other.complete(request(run), Reply)
    expected = ErrorCode.deadline_exceeded if limit == "deadline" else ErrorCode.budget_exceeded
    assert err.value.code == expected and not calls


async def test_tariff_or_key_missing_preflight_has_no_model_calls(db):
    called = []
    gateway = LLMGateway(
        BudgetLedger(db),
        config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True),
        transport=httpx.MockTransport(lambda req: called.append(req)),
    )
    with pytest.raises(GatewayError):
        await gateway.start_run(uuid4().hex)
    assert not called


async def test_captured_tariff_precision_survives_operator_reconciliation(db):
    gateway, run = await make_gateway(db, lambda _: httpx.Response(200, json=envelope(usage=None)))
    precise = Tariff(
        MODEL, "precise-test", Decimal("1.123456789123"), Decimal("2.987654321987"), True
    )
    gateway.tariffs[MODEL] = precise
    req = request(run)
    with pytest.raises(GatewayError):
        await gateway.complete(req, Reply)
    # Reconciliation uses the pinned snapshot even if runtime configuration changes.
    gateway.tariffs[MODEL] = tariff()
    # Accounting of provider-confirmed legacy spend must remain possible before
    # capability approval for any NEW generation. No network is involved.
    gateway.config = LLMConfig(live_enabled=True, billable_output_cap_confirmed=False)
    result = await gateway.reconcile(req.call_id, input_tokens=100, output_tokens=10)
    assert result.cost_rub == precise.quote(input_tokens=100, max_output_tokens=10)


async def test_reasoning_subset_is_settled_at_output_rate_exactly_once(db):
    gateway, run = await make_gateway(
        db,
        lambda _: httpx.Response(
            200,
            json=envelope(
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "completion_tokens_details": {"reasoning_tokens": 6},
                }
            ),
        ),
    )
    result = await gateway.complete(request(run), Reply)
    assert result.usage.output_tokens == 10
    assert result.usage.cost_rub == tariff().quote(input_tokens=100, max_output_tokens=10)


@pytest.mark.parametrize(
    "first", ["https://changellenge.up.railway.app/v1", "https://api.alfahack-gateway26.ru/v1"]
)
async def test_ambiguous_reasoning_holds_one_shared_gate_across_both_addresses(db, first):
    second = (
        "https://api.alfahack-gateway26.ru/v1"
        if "railway" in first
        else "https://changellenge.up.railway.app/v1"
    )
    seen = []

    def reply(req):
        seen.append(str(req.url))
        return httpx.Response(
            200,
            json=envelope(
                usage={"prompt_tokens": 100, "completion_tokens": 10, "reasoning_tokens": 6}
            ),
        )

    key = SecretStr("offline-cross-address-" + uuid4().hex)

    def client(url):
        return LLMGateway(
            BudgetLedger(db),
            config=LLMConfig(live_enabled=True, base_url=url, billable_output_cap_confirmed=True),
            api_key=key,
            tariffs={MODEL: tariff()},
            transport=httpx.MockTransport(reply),
        )

    a, b = client(first), client(second)
    assert a.scope == b.scope
    run1, run2 = uuid4().hex, uuid4().hex
    await a.start_run(run1)
    await b.start_run(run2)
    req = request(run1)
    with pytest.raises(GatewayError) as stopped:
        await a.complete(req, Reply)
    assert stopped.value.code == ErrorCode.llm_outcome_unknown
    _, call = await record(db, req.call_id)
    assert call["outcome"] == "uncertain"
    with pytest.raises(GatewayError) as busy:
        await b.complete(request(run2), Reply)
    assert busy.value.code == ErrorCode.llm_busy
    assert seen == [first + "/chat/completions"]
