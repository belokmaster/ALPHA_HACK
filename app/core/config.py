from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str = "postgresql+asyncpg://alfa:alfa_local@db:5432/alfa"
    app_secret: SecretStr = SecretStr("")
    demo_mode: bool = False
    live_llm_enabled: bool = False
    llm_api_key: SecretStr = SecretStr("")
    llm_runtime_file: str = "config/llm-runtime.json"
    llm_billable_output_cap_confirmed: bool = False
    llm_live_session_id: str = ""
    app_code_sha: str = "unknown"
    llm_tariff_file: str = "config/llm-tariffs.json"
    llm_base_url: str = "https://changellenge.up.railway.app/v1"
    analysis_mode: Literal["evidence_only", "gateway", "controlled"] = "evidence_only"
    controlled_llm_response: Literal[
        "ok", "invalid_json", "timeout", "missing_usage", "budget", "reject_claims"
    ] = "ok"
    cookie_secure: bool = False
    app_origin: str = "http://localhost:8000"
    session_hours: int = 8
    lease_seconds: int = 90
    heartbeat_seconds: int = 10
    max_attempts: int = 2


settings = Settings()
