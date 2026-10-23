"""Runtime construction; never reads .env or starts a network request."""

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.budgets.service import BudgetLedger, Tariff
from app.core.config import Settings
from app.core.errors import AppError
from app.llm.config import LLMConfig
from app.llm.gateway import LLMGateway
from app.llm.runtime import Mode, RuntimeConfig, load_runtime, quote_stages
from app.llm.session import risk_reserve
from contracts.v1 import ErrorCode


class TariffEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    version: str = Field(min_length=1, max_length=128)
    input_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    verified: bool = Field(default=False, strict=True)
    evidence: str | None = None  # Legacy input compatibility; not used as authorization.
    multiplier: Decimal = Field(default=Decimal("1"), ge=1, allow_inf_nan=False)


class TariffFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tariffs: list[TariffEntry]


def load_tariffs(path: Path) -> dict[str, Tariff]:
    try:
        data = TariffFile.model_validate(json.loads(path.read_text()))
        result = {}
        for entry in data.tariffs:
            if entry.model_id in result:
                raise ValueError("Duplicate tariff")
            values = entry.model_dump(exclude={"evidence", "multiplier"})
            if "multiplier" in entry.model_fields_set:
                values.update(
                    version=entry.version + ":multiplier:" + str(entry.multiplier.normalize()),
                    estimate_multiplier=entry.multiplier,
                    estimate_confirmed=True,
                )
            if len(values["version"]) > 128:
                raise ValueError("Effective tariff version exceeds durable pin size")
            tariff = Tariff(**values)
            tariff.require_verified()
            result[entry.model_id] = tariff
        return result
    except (ValueError, OSError, AppError):
        raise AppError(
            ErrorCode.budget_unknown, "Verified tariff configuration unavailable", 503
        ) from None


def create_gateway(
    engine: AsyncEngine | None,
    settings: Settings | None = None,
    *,
    runtime: RuntimeConfig | None = None,
    mode: Mode = "adaptive",
) -> LLMGateway:
    """Require a ledger engine handle in every mode, without opening a connection.

    Disabled construction still avoids keys/tariff files. None is an invalid
    dependency, not permission to select a different mode or invent a ledger.
    """
    settings = settings or Settings()
    if engine is None:
        controlled = settings.analysis_mode == "controlled"
        raise AppError(
            ErrorCode.forbidden if controlled else ErrorCode.backend_unavailable,
            "Controlled mode requires isolated alfa_test engine"
            if controlled
            else "Shared ledger engine is required",
            403 if controlled else 503,
        )
    runtime = runtime or load_runtime(settings)
    if settings.analysis_mode == "controlled":
        # Controlled mode cannot use a real key or production/demo working database.
        if (
            not settings.demo_mode
            or settings.live_llm_enabled
            or settings.llm_api_key.get_secret_value()
            or engine.url.database != "alfa_test"
        ):
            raise AppError(
                ErrorCode.forbidden,
                "Controlled mode requires isolated alfa_test, demo, no live/key",
                403,
            )
        from app.llm.controlled import transport

        rate = Decimal("100000") if settings.controlled_llm_response == "budget" else Decimal("1")
        models = {runtime.stage(p).model_id for p in runtime.purposes(mode)}
        return LLMGateway(
            BudgetLedger(engine),
            config=LLMConfig(
                live_enabled=True,
                billable_output_cap_confirmed=True,
                base_url=settings.llm_base_url,
            ),
            runtime=runtime,
            mode=mode,
            api_key=SecretStr("controlled-offline-key-not-a-provider-secret"),
            tariffs={
                model: Tariff(
                    model_id=model,
                    version="controlled-fixture-1",
                    input_rub_per_million=rate,
                    output_rub_per_million=rate,
                    verified=True,
                )
                for model in models
            },
            transport=transport(settings.controlled_llm_response),
        )
    # Disabled synthetic mode does not need/read tariff files or a key.
    live = settings.live_llm_enabled
    tariffs = load_tariffs(Path(settings.llm_tariff_file)) if live else {}
    if live:
        quotes = quote_stages(runtime, mode, tariffs)
        if (
            sum(
                (
                    risk_reserve(Decimal(q["maximum_reserve_rub"]))
                    if settings.llm_live_session_id
                    else Decimal(q["maximum_reserve_rub"])
                    for q in quotes
                ),
                Decimal("0"),
            )
            > runtime.limits.budget_rub
        ):
            raise AppError(ErrorCode.budget_exceeded, "Configured stages exceed run reserve", 429)
    return LLMGateway(
        BudgetLedger(engine),
        config=LLMConfig(
            live_enabled=live,
            base_url=settings.llm_base_url,
            billable_output_cap_confirmed=settings.llm_billable_output_cap_confirmed,
            live_session_id=settings.llm_live_session_id,
        ),
        runtime=runtime,
        mode=mode,
        api_key=settings.llm_api_key if live else SecretStr(""),
        tariffs=tariffs,
    )
