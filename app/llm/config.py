"""Exact IDs from Новостная_аналитика_Инструкция_по_ключам.pdf (read locally).

Allowed does not imply that availability, prices or usage accounting were verified.
"""

from dataclasses import dataclass

BASE_URL = "https://changellenge.up.railway.app/v1"
ALLOWED_BASE_URLS = frozenset({BASE_URL, "https://api.alfahack-gateway26.ru/v1"})
ALLOWED_MODEL_IDS = frozenset(
    {
        "google/gemma-4-26b-a4b-it",
        "z-ai/glm-5.3-flash",
        "qwen/qwen3.6-35b-a3b",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3-vl-30b-a3b-instruct",
        "moonshotai/kimi-k2.6",
    }
)


@dataclass(frozen=True)
class LLMConfig:
    live_enabled: bool = False
    billable_output_cap_confirmed: bool = False
    base_url: str = BASE_URL
    max_retries: int = 0
    live_session_id: str = ""

    def __post_init__(self) -> None:
        if self.base_url not in ALLOWED_BASE_URLS or self.max_retries != 0:
            raise ValueError("Allowlisted endpoint and disabled SDK retries required")
