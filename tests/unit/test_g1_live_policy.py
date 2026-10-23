from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.budgets.service import BudgetLedger, Tariff
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.g1_live import LiveG1Client, authorize_live_g1
from app.llm.gateway import LLMGateway
from contracts.v1 import ErrorCode, LLMRequest

MODEL = "deepseek/deepseek-v4.1-flash"


class Reply(BaseModel):
    answer: int


def gateway(*, prices=True, rate="1"):
    return LLMGateway(
        BudgetLedger(create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")),
        config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True),
        api_key=SecretStr("offline-unit-only"),
        tariffs={MODEL: Tariff(MODEL, "offline-1", Decimal(rate), Decimal(rate), True)}
        if prices
        else {},
        mode="two_call_smoke",
        transport=httpx.MockTransport(lambda r: pytest.fail("Unit policy must never send")),
    )


def test_unknown_tariff_and_excess_cost_stop_before_transport():
    with pytest.raises(AppError) as error:
        authorize_live_g1(gateway(prices=False), Decimal("5"))
    assert error.value.code == ErrorCode.budget_unknown
    with pytest.raises(AppError) as error:
        authorize_live_g1(gateway(rate="100000"), Decimal("5"))
    assert error.value.code == ErrorCode.budget_exceeded


@pytest.mark.parametrize("cap", ["0", "-1", "5.01", "NaN", "Infinity"])
def test_only_bounded_finite_approval(cap):
    with pytest.raises(AppError):
        authorize_live_g1(gateway(), Decimal(cap))


def test_worker_rejects_tariff_drift():
    original = gateway()
    auth = authorize_live_g1(original, Decimal("5"))
    with pytest.raises(AppError, match="Pinned G1 tariff"):
        LiveG1Client(gateway(rate="2"), "job", auth)


@pytest.mark.parametrize("purpose", ["verify", "repair", "retry", "fallback"])
async def test_no_verify_without_successful_draft_or_other_purpose(purpose):
    g = gateway()
    client = LiveG1Client(g, "job", authorize_live_g1(g, Decimal("5")))
    with pytest.raises(AppError):
        await client.complete(
            LLMRequest(
                run_id="job_run",
                call_id="job_" + purpose,
                model_id=MODEL,
                purpose=purpose,
                prompt="Unit fixture",
                max_output_tokens=100,
            ),
            Reply,
        )


async def test_foreign_call_id_cannot_expand_number_of_durable_calls():
    g = gateway()
    client = LiveG1Client(g, "job", authorize_live_g1(g, Decimal("5")))
    with pytest.raises(AppError):
        await client.complete(
            LLMRequest(
                run_id="job_run",
                call_id=uuid4().hex,
                model_id=MODEL,
                purpose="draft",
                prompt="Unit fixture",
                max_output_tokens=900,
            ),
            Reply,
        )


async def test_live_job_policy_is_rejected_before_loading_data_on_wrong_worker():
    from app.jobs.analysis import run_analysis

    with pytest.raises(AppError) as error:
        await run_analysis(
            None,
            {
                "payload": {
                    "policy_version": "integration-conditional-1",
                    "live_g1_authorization": {},
                }
            },
        )
    assert error.value.code == ErrorCode.data_conflict
