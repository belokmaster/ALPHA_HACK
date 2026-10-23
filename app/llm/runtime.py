"""Immutable, non-secret runtime policy. No env loading, database or HTTP here."""

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.budgets.service import Tariff
from app.core.config import Settings
from app.core.errors import AppError
from app.llm.config import ALLOWED_MODEL_IDS
from contracts.v1 import ErrorCode

Mode = Literal["adaptive", "two_call_smoke"]
Stage = Literal["plan", "draft", "verify"]


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StageConfig(StrictConfig):
    model_id: str
    max_output_tokens: int = Field(ge=1, le=4000)
    reasoning_effort: Literal["none", "low", "high"] | None = None

    @field_validator("model_id")
    @classmethod
    def allowed(cls, value: str) -> str:
        if value not in ALLOWED_MODEL_IDS:
            raise ValueError("Model outside instruction allowlist")
        return value


class Stages(StrictConfig):
    plan: StageConfig
    draft: StageConfig
    verify: StageConfig


class Timeouts(StrictConfig):
    transport_seconds: float = Field(gt=0, le=30)
    call_seconds: float = Field(gt=0, le=30)
    run_seconds: float = Field(gt=0, le=300)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.transport_seconds <= self.call_seconds <= self.run_seconds:
            raise ValueError("Require transport <= call <= run deadline")
        return self


class Limits(StrictConfig):
    transitions: int = Field(ge=1, le=12)
    tools: int = Field(ge=1, le=6)
    repeated_searches: int = Field(ge=0, le=1)
    input_per_call: int = Field(ge=1, le=8000)
    budget_rub: int = Field(ge=1, le=5)


class ModeLimits(StrictConfig):
    max_model_calls: int = Field(ge=1, le=4)
    input_tokens: int = Field(ge=1, le=24000)
    output_tokens: int = Field(ge=1, le=4000)


class Modes(StrictConfig):
    adaptive: ModeLimits
    two_call_smoke: ModeLimits


class RuntimeConfig(StrictConfig):
    schema_version: Literal[1]
    version: str = Field(min_length=1, max_length=80)
    followup_policy: Literal["inherit_stages"]
    stages: Stages
    timeouts: Timeouts
    limits: Limits
    modes: Modes

    @model_validator(mode="after")
    def envelopes(self) -> Self:
        for name in ("adaptive", "two_call_smoke"):
            mode = self.for_mode(name)
            purposes = self.purposes(name)
            if mode.max_model_calls != len(purposes):
                raise ValueError("Adaptive has 3 stages; two-call smoke has exactly 2")
            if mode.input_tokens < len(purposes) * self.limits.input_per_call:
                raise ValueError("Input budget cannot cover configured per-stage envelopes")
            if mode.output_tokens < sum(self.stage(p).max_output_tokens for p in purposes):
                raise ValueError("Output budget cannot cover configured stages")
        return self

    def stage(self, purpose: Stage) -> StageConfig:
        return getattr(self.stages, purpose)  # type: ignore[no-any-return]

    def for_mode(self, mode: Mode) -> ModeLimits:
        return getattr(self.modes, mode)  # type: ignore[no-any-return]

    @staticmethod
    def purposes(mode: Mode) -> tuple[Stage, ...]:
        return ("plan", "draft", "verify") if mode == "adaptive" else ("draft", "verify")

    @property
    def sha256(self) -> str:
        data = json.dumps(self.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()

    def snapshot(self, mode: Mode) -> dict[str, object]:
        return {"config": self.model_dump(mode="json", exclude_none=True), "sha256": self.sha256, "mode": mode}


def load_runtime(settings: Settings | None = None) -> RuntimeConfig:
    settings = settings or Settings()
    try:
        return RuntimeConfig.model_validate_json(Path(settings.llm_runtime_file).read_text())
    except (OSError, ValueError):
        raise AppError(ErrorCode.validation, "Invalid LLM runtime configuration", 503) from None


def pinned_runtime(snapshot: object) -> tuple[RuntimeConfig, Mode]:
    try:
        pin = RuntimeSnapshot.model_validate(snapshot)
        return pin.config, pin.mode
    except ValueError:
        raise AppError(ErrorCode.data_conflict, "Pinned LLM runtime is invalid", 409) from None


class RuntimeSnapshot(StrictConfig):
    config: RuntimeConfig
    sha256: str
    mode: Mode

    @model_validator(mode="after")
    def digest(self) -> Self:
        if self.config.sha256 != self.sha256:
            raise ValueError("Runtime hash differs")
        return self


def quote_stages(
    runtime: RuntimeConfig, mode: Mode, tariffs: dict[str, Tariff]
) -> list[dict[str, str | int]]:
    result: list[dict[str, str | int]] = []
    for purpose in runtime.purposes(mode):
        stage = runtime.stage(purpose)
        tariff = tariffs.get(stage.model_id)
        if tariff is None:
            raise AppError(ErrorCode.budget_unknown, "Tariff missing for " + stage.model_id, 503)
        tariff.require_verified()
        if tariff.model_id != stage.model_id:
            raise AppError(ErrorCode.data_conflict, "Tariff model differs", 409)
        quote = tariff.estimate(
            input_tokens=runtime.limits.input_per_call,
            max_output_tokens=stage.max_output_tokens,
        )
        if quote <= 0:
            raise AppError(ErrorCode.budget_unknown, "Positive maximum reserve required", 503)
        result.append(
            {
                "stage": purpose,
                "model_id": stage.model_id,
                "input_tokens": runtime.limits.input_per_call,
                "max_output_tokens": stage.max_output_tokens,
                "maximum_reserve_rub": str(quote),
                "nominal_tariff_rub": str(tariff.quote(
                    input_tokens=runtime.limits.input_per_call,
                    max_output_tokens=stage.max_output_tokens,
                )),
                "estimate_multiplier": str(tariff.estimate_multiplier),
                "estimate_confirmation": "owner_accepted" if tariff.estimate_confirmed else "uncalibrated",
                "tariff_version": tariff.version,
            }
        )
    return result
