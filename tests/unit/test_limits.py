from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agent.limits import RunBudget, RunLimits
from app.budgets.service import Tariff, budget_day_at
from app.core.errors import AppError
from app.llm.config import ALLOWED_MODEL_IDS, LLMConfig
from app.llm.unavailable import UnavailableLLMClient, fixture_usage
from contracts.v1 import ErrorCode, LLMRequest, ModelUsage


@pytest.mark.parametrize(("method", "limit"), [("transition", 12), ("tool", 6)])
def test_action_caps_stop_before_execution(method: str, limit: int) -> None:
    run = RunBudget()
    action = getattr(run, method)
    for _ in range(limit):
        action()
    with pytest.raises(AppError) as error:
        action()
    assert error.value.code == ErrorCode.budget_exceeded


def test_fallback_repair_and_retry_share_model_and_token_limits() -> None:
    run = RunBudget()
    # Primary, fallback, repair and retry are four attempts, regardless of their result.
    for _ in range(4):
        run.model(input_tokens=6000, output_tokens=1000)
    assert (run.models_used, run.input_tokens_used, run.output_tokens_used) == (4, 24_000, 4000)
    with pytest.raises(AppError):
        run.model(input_tokens=1, output_tokens=1)
    assert run.models_used == 4


@pytest.mark.parametrize(("inputs", "outputs"), [(24_001, 0), (0, 4001)])
def test_token_caps_are_checked_before_attempt(inputs: int, outputs: int) -> None:
    run = RunBudget()
    with pytest.raises(AppError):
        run.model(input_tokens=inputs, output_tokens=outputs)
    assert run.models_used == 0


def test_only_one_repeated_search_and_search_consumes_tool_budget() -> None:
    run = RunBudget()
    run.search("Interest  rates")
    run.search(" interest rates ")
    run.search("steel")
    with pytest.raises(AppError):
        run.search("STEEL")
    assert run.tools_used == 3
    assert run.repeated_searches_used == 1


def test_deadline_uses_injected_clock() -> None:
    now = [100.0]
    run = RunBudget(clock=lambda: now[0])
    now[0] += 59.999
    run.transition()
    now[0] += 0.001
    with pytest.raises(AppError) as error:
        run.tool()
    assert error.value.code == ErrorCode.deadline_exceeded
    assert run.tools_used == 0


def test_limits_can_only_be_tightened() -> None:
    with pytest.raises(ValueError):
        RunLimits(models=5)
    with pytest.raises(ValueError):
        RunLimits(seconds=float("nan"))
    with pytest.raises(ValueError):
        RunLimits(seconds=301)
    run = RunBudget(limits=RunLimits(models=1))
    run.model(input_tokens=1, output_tokens=1)
    with pytest.raises(AppError):
        run.model(input_tokens=1, output_tokens=1)


def test_budget_day_changes_at_three_moscow() -> None:
    assert str(budget_day_at(datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC))) == "2026-10-20"
    assert str(budget_day_at(datetime(2026, 10, 21, 0, 0, 0, tzinfo=UTC))) == "2026-10-21"
    with pytest.raises(ValueError):
        budget_day_at(datetime(2026, 10, 21))


def test_unknown_tariff_never_means_zero() -> None:
    tariff = Tariff(model_id="deepseek/deepseek-v4.1-flash", version="unknown")
    with pytest.raises(AppError) as error:
        tariff.quote(input_tokens=100, max_output_tokens=100)
    assert error.value.code == ErrorCode.budget_unknown


def test_tariff_quote_uses_decimal_and_rounds_up() -> None:
    tariff = Tariff(
        model_id="deepseek/deepseek-v4.1-flash",
        version="synthetic-test-only",
        input_rub_per_million=Decimal("1.1"),
        output_rub_per_million=Decimal("2.2"),
        verified=True,
    )
    assert tariff.quote(input_tokens=1, max_output_tokens=1) == Decimal("0.000004")


@pytest.mark.parametrize(
    ("enabled", "code"),
    [(False, ErrorCode.backend_unavailable), (True, ErrorCode.feature_not_implemented)],
)
async def test_llm_port_is_honestly_unavailable(enabled: bool, code: ErrorCode) -> None:
    client = UnavailableLLMClient(LLMConfig(live_enabled=enabled, billable_output_cap_confirmed=True))
    with pytest.raises(AppError) as error:
        await client.complete(
            LLMRequest(
                run_id="run",
                call_id="call",
                model_id="deepseek/deepseek-v4.1-flash",
                prompt="fixture only",
            ),
            ModelUsage,
        )
    assert error.value.code == code


async def test_unlisted_model_is_rejected_and_fixture_has_no_usage() -> None:
    client = UnavailableLLMClient()
    with pytest.raises(AppError) as error:
        await client.complete(
            LLMRequest(run_id="run", call_id="call", model_id="unapproved/model", prompt="test"),
            ModelUsage,
        )
    assert error.value.code == ErrorCode.validation
    usage = fixture_usage()
    assert usage.model_id is None and usage.usage_status == "not_called"
    assert usage.cost_rub is None and usage.input_tokens is None
    assert len(ALLOWED_MODEL_IDS) == 6


def test_no_configurable_endpoint_or_hidden_sdk_retries() -> None:
    with pytest.raises(ValueError):
        LLMConfig(base_url="https://different.invalid/v1")
    with pytest.raises(ValueError):
        LLMConfig(max_retries=1)
