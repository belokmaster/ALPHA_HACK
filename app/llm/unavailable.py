from typing import TypeVar

from pydantic import BaseModel

from app.core.errors import AppError
from app.llm.config import ALLOWED_MODEL_IDS, LLMConfig
from contracts.v1 import ErrorCode, LLMRequest, LLMResult, ModelUsage

Output = TypeVar("Output", bound=BaseModel)


class UnavailableLLMClient:
    """Implements LLMClient without a transport or any access to API keys."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()

    async def start_run(self, run_id: str, *, job_id: str | None = None) -> None:
        if not self.config.live_enabled:
            raise AppError(ErrorCode.backend_unavailable, "Live LLM is disabled", 503)
        raise AppError(ErrorCode.feature_not_implemented, "Use shared gateway", 501)

    async def complete(
        self, request: LLMRequest, response_model: type[Output]
    ) -> LLMResult[Output]:
        if request.model_id not in ALLOWED_MODEL_IDS:
            raise AppError(ErrorCode.validation, "Model is outside the configured allowlist", 422)
        if not self.config.live_enabled:
            raise AppError(ErrorCode.backend_unavailable, "Live LLM is disabled", 503)
        raise AppError(
            ErrorCode.feature_not_implemented,
            "Use the shared gateway factory for explicitly enabled live calls",
            501,
        )


def fixture_usage() -> ModelUsage:
    """Fixture execution is never recorded as provider consumption in the ledger."""
    return ModelUsage(model_id=None, usage_status="not_called")
