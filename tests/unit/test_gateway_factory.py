import json

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import create_gateway
from contracts.v1 import ErrorCode


@pytest.mark.parametrize(
    "mode,live,code",
    [
        ("evidence_only", False, ErrorCode.backend_unavailable),
        ("controlled", False, ErrorCode.forbidden),
        ("gateway", True, ErrorCode.backend_unavailable),
    ],
)
def test_missing_engine_fails_before_tariff_or_transport(mode, live, code, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing engine must not access tariffs or construct transport")

    monkeypatch.setattr("app.llm.factory.load_tariffs", forbidden)
    monkeypatch.setattr("app.llm.factory.LLMGateway", forbidden)
    cfg = Settings(
        analysis_mode=mode,
        live_llm_enabled=live,
        demo_mode=True,
        llm_api_key=SecretStr(""),
        llm_tariff_file="missing",
    )
    with pytest.raises(AppError) as error:
        create_gateway(None, cfg)
    assert error.value.code == code


async def test_controlled_with_engine_still_uses_only_offline_transport():
    db = create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")
    try:
        cfg = Settings(
            analysis_mode="controlled",
            demo_mode=True,
            live_llm_enabled=False,
            llm_api_key=SecretStr(""),
        )
        gateway = create_gateway(db, cfg)
        assert gateway.engine is db and gateway.config.live_enabled
        assert (
            gateway.tariffs[gateway.runtime.stages.draft.model_id].version == "controlled-fixture-1"
        )
    finally:
        await db.dispose()


@pytest.mark.parametrize("known", [True, False])
async def test_live_factory_with_engine_constructs_locally_and_unknown_price_blocks(
    known, tmp_path, monkeypatch
):
    async def forbidden(*args, **kwargs):
        pytest.fail("Factory/preflight must not send HTTP")

    monkeypatch.setattr("app.llm.transport.ChatTransport.send", forbidden)
    model = "deepseek/deepseek-v4.1-flash"
    path = tmp_path / "tariffs.json"
    path.write_text(
        json.dumps(
            {
                "tariffs": [
                    {
                        "model_id": model,
                        "version": "unit-fixture-not-real",
                        "verified": True,
                        "input_rub_per_million": "1",
                        "output_rub_per_million": "1",
                        "evidence": "Unit fixture only, no provider tariff claim",
                    }
                ]
                if known
                else []
            }
        )
    )
    db = create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")
    try:
        cfg = Settings(
            analysis_mode="gateway",
            live_llm_enabled=True,
            llm_api_key=SecretStr("offline-factory-test"),
            llm_tariff_file=str(path),
        )
        if not known:
            with pytest.raises(AppError) as error:
                create_gateway(db, cfg)
            assert error.value.code == ErrorCode.budget_unknown
        else:
            gateway = create_gateway(db, cfg)
            assert gateway.engine is db and gateway.config.live_enabled
            assert gateway.tariffs[model].version == "unit-fixture-not-real"

    finally:
        await db.dispose()
