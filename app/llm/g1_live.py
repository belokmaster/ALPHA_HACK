"""Pinned two-call envelope over the one shared gateway, never a fallback client."""

from decimal import Decimal
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError
from app.llm.gateway import LLMGateway
from app.llm.runtime import RuntimeSnapshot, quote_stages
from app.llm.session import risk_reserve
from app.llm.transport import input_bound, messages_for
from contracts.v1 import ErrorCode, LLMRequest, LLMResult

POLICY: Literal["g1-live-two-call-2"] = "g1-live-two-call-2"
Output = TypeVar("Output", bound=BaseModel)


class PinnedStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: str
    max_output_tokens: int = Field(strict=True, ge=1, le=4000)
    tariff_version: str
    input_rate: Decimal = Field(ge=0, allow_inf_nan=False)
    output_rate: Decimal = Field(ge=0, allow_inf_nan=False)


class PinnedStages(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft: PinnedStage
    verify: PinnedStage


class LiveG1Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy: Literal["g1-live-two-call-2"] = POLICY
    runtime: RuntimeSnapshot
    base_url: str
    session_id: str | None = None
    stages: PinnedStages
    max_calls: Literal[2] = 2
    budget_rub: Decimal = Field(gt=0, le=5, allow_inf_nan=False)
    maximum_reserve_rub: Decimal = Field(gt=0, le=5, allow_inf_nan=False)


def authorize_live_g1(gateway: LLMGateway, budget_rub: Decimal) -> LiveG1Authorization:
    gateway._enabled()  # Local opt-in/key guards, never HTTP.
    gateway.require_output_cap()
    if gateway.mode != "two_call_smoke":
        raise AppError(ErrorCode.data_conflict, "G1 requires two_call_smoke runtime mode", 409)
    runtime = gateway.runtime
    quotes = quote_stages(runtime, "two_call_smoke", gateway.tariffs)
    maximum = sum(
        (
            risk_reserve(Decimal(q["maximum_reserve_rub"]))
            if gateway.session
            else Decimal(q["maximum_reserve_rub"])
            for q in quotes
        ),
        Decimal("0"),
    )
    if (
        not budget_rub.is_finite()
        or not 0 < budget_rub <= runtime.limits.budget_rub
        or maximum > budget_rub
    ):
        raise AppError(ErrorCode.budget_exceeded, "Two-call reserve exceeds approved budget", 429)
    stages = {}
    for purpose in ("draft", "verify"):
        stage = runtime.stage(purpose)
        tariff = gateway.tariffs[stage.model_id]
        assert tariff.input_rub_per_million is not None
        assert tariff.output_rub_per_million is not None
        stages[purpose] = PinnedStage(
            model_id=stage.model_id,
            max_output_tokens=stage.max_output_tokens,
            tariff_version=tariff.version,
            input_rate=tariff.input_rub_per_million,
            output_rate=tariff.output_rub_per_million,
        )
    return LiveG1Authorization(
        runtime=RuntimeSnapshot.model_validate(runtime.snapshot("two_call_smoke")),
        base_url=gateway.config.base_url,
        session_id=gateway.config.live_session_id or None,
        stages=PinnedStages(**stages),
        budget_rub=budget_rub,
        maximum_reserve_rub=maximum,
    )


class LiveG1Client:
    def __init__(self, gateway: LLMGateway, job_id: str, authorization: LiveG1Authorization):
        if authorize_live_g1(gateway, authorization.budget_rub) != authorization:
            raise AppError(
                ErrorCode.data_conflict, "Pinned G1 tariff/runtime/endpoint changed", 409
            )
        self.gateway, self.job_id = gateway, job_id
        self.authorization = authorization
        self.draft_succeeded = False
        self.attempted: set[str] = set()

    async def start_run(self, run_id: str, *, job_id: str | None = None) -> None:
        if run_id != self.job_id + "_run" or job_id != self.job_id:
            raise AppError(ErrorCode.validation, "G1 run identity differs", 422)
        await self.gateway.start_run(run_id, job_id=job_id)

    async def complete(
        self, request: LLMRequest, response_model: type[Output]
    ) -> LLMResult[Output]:
        purpose = request.purpose
        if purpose not in {"draft", "verify"}:
            raise AppError(ErrorCode.budget_exceeded, "G1 permits draft then verify only", 429)
        stage = getattr(self.authorization.stages, purpose)
        if (
            purpose in self.attempted
            or request.run_id != self.job_id + "_run"
            or request.call_id != self.job_id + "_" + purpose
            or request.model_id != stage.model_id
            or request.max_output_tokens > stage.max_output_tokens
            or input_bound(messages_for(request.prompt, response_model))
            > self.authorization.runtime.config.limits.input_per_call
            or (purpose == "verify" and not self.draft_succeeded)
        ):
            raise AppError(
                ErrorCode.budget_exceeded, "G1 permits draft then verify only; no retry", 429
            )
        self.attempted.add(purpose)
        result = await self.gateway.complete(request, response_model)
        if purpose == "draft":
            self.draft_succeeded = True
        return result
