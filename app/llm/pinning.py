"""Server-selected session and tariffs pinned at enqueue, no network or secrets."""

from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.llm.factory import load_tariffs
from app.llm.runtime import RuntimeConfig, quote_stages


def live_pin(settings: Settings, runtime: RuntimeConfig) -> dict[str, Any] | None:
    if not settings.live_llm_enabled or settings.analysis_mode != "gateway":
        return None
    tariffs = load_tariffs(Path(settings.llm_tariff_file))
    quote_stages(runtime, "adaptive", tariffs)
    return {
        "session_id": settings.llm_live_session_id,
        "code_sha": settings.app_code_sha,
        "base_url": settings.llm_base_url,
        "tariffs": {
            model: {
                "version": tariffs[model].version,
                "input_rate": str(tariffs[model].input_rub_per_million),
                "output_rate": str(tariffs[model].output_rub_per_million),
            }
            for model in {runtime.stage(p).model_id for p in runtime.purposes("adaptive")}
        },
    }
