"""Configuration is operational policy, not a model quality experiment."""

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine
from test_agent_engine import Planning, setup
from test_analysis_engine import input_data as baseline_data

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import create_gateway, load_tariffs
from app.llm.g1_live import LiveG1Client, authorize_live_g1
from app.llm.runtime import RuntimeConfig, load_runtime, pinned_runtime, quote_stages
from contracts.v1 import ErrorCode

GEMMA = "google/gemma-4-26b-a4b-it"
GLM = "z-ai/glm-5.3-flash"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"


def mixed_runtime():
    raw = load_runtime().model_dump()
    for purpose, model in zip(("plan", "draft", "verify"), (GEMMA, DEEPSEEK, GLM), strict=True):
        raw["stages"][purpose]["model_id"] = model
    return RuntimeConfig.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"schema_version": 2},
        {"followup_policy": "guessed"},
        {"stages": {"draft": {"model_id": "gpt-unknown", "max_output_tokens": 900}}},
        {"timeouts": {"transport_seconds": 13, "call_seconds": 12, "run_seconds": 60}},
        {"timeouts": {"transport_seconds": 12, "call_seconds": 6, "run_seconds": 60}},
        {"timeouts": {"transport_seconds": 31, "call_seconds": 31, "run_seconds": 300}},
        {"timeouts": {"run_seconds": 301}},
        {"limits": {"budget_rub": 6}},
        {"limits": {"input_per_call": True}},
        {"limits": {"tools": 7}},
        {"modes": {"two_call_smoke": {"max_model_calls": 3}}},
        {"modes": {"two_call_smoke": {"output_tokens": 100}}},
        {"stages": {"plan": {"max_output_tokens": 0}}},
    ],
)
def test_invalid_configuration_fails_closed(change):
    raw = load_runtime().model_dump()

    def merge(dst, patch):
        for k, value in patch.items():
            if isinstance(value, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], value)
            else:
                dst[k] = value

    merge(raw, change)
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(raw)


def test_strict_schema_export_matches_and_config_is_immutable():
    assert (
        json.loads(Path("config/llm-runtime.schema.json").read_text())
        == RuntimeConfig.model_json_schema()
    )
    r = load_runtime()
    with pytest.raises(ValidationError):
        r.stages.draft.model_id = GLM
    pin = r.snapshot("adaptive")
    assert pinned_runtime(pin) == (r, "adaptive")
    pin["sha256"] = "tampered"
    with pytest.raises(AppError):
        pinned_runtime(pin)


def test_reasoning_effort_is_pinned_and_null_preserves_legacy_config_hash():
    raw = load_runtime().model_dump()
    for stage in raw["stages"].values():
        stage.pop("reasoning_effort", None)
    legacy = RuntimeConfig.model_validate(raw)
    raw["stages"]["plan"]["reasoning_effort"] = None
    assert RuntimeConfig.model_validate(raw).sha256 == legacy.sha256
    raw["stages"]["plan"]["reasoning_effort"] = "none"
    changed = RuntimeConfig.model_validate(raw)
    assert changed.sha256 != legacy.sha256
    assert pinned_runtime(changed.snapshot("adaptive"))[0].stages.plan.reasoning_effort == "none"


def test_offline_quotes_use_each_stage_tariff_and_approved_units():
    tariffs = load_tariffs(Path("config/llm-tariffs.json"))
    runtime = mixed_runtime()
    quotes = quote_stages(runtime, "adaptive", tariffs)
    assert [q["model_id"] for q in quotes] == [GEMMA, DEEPSEEK, GLM]
    for q in quotes:
        t = tariffs[q["model_id"]]
        assert Decimal(q["maximum_reserve_rub"]) == t.estimate(
            input_tokens=8000, max_output_tokens=q["max_output_tokens"]
        )
    default = quote_stages(load_runtime(), "two_call_smoke", tariffs)
    assert sum(Decimal(q["maximum_reserve_rub"]) for q in default) == Decimal("0.455143")
    assert len(tariffs) == 6
    tariffs.pop(GLM)
    with pytest.raises(AppError) as err:
        quote_stages(runtime, "adaptive", tariffs)
    assert err.value.code == ErrorCode.budget_unknown


async def test_app_worker_and_cli_resolve_same_config_without_key_or_network(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("No HTTP/DB while resolving configuration")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    r = mixed_runtime()
    path = tmp_path / "runtime.json"
    path.write_text(r.model_dump_json())
    cfg = Settings(
        llm_runtime_file=str(path),
        analysis_mode="controlled",
        demo_mode=True,
        llm_base_url="https://api.alfahack-gateway26.ru/v1",
    )
    db = create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")
    try:
        app_config = load_runtime(cfg)
        gateway = create_gateway(db, cfg)
        assert gateway.runtime == app_config == r
        assert {r.stage(p).model_id for p in r.purposes("adaptive")} == set(gateway.tariffs)
        out = subprocess.check_output(
            [sys.executable, "-m", "scripts.llm_config"],
            text=True,
            env={
                **os.environ,
                "LLM_API_KEY": "",
                "LIVE_LLM_ENABLED": "false",
                "LLM_RUNTIME_FILE": str(path),
                "LLM_BASE_URL": cfg.llm_base_url,
            },
        )
        cli = json.loads(out)
        assert cli["runtime_sha256"] == gateway.runtime.sha256 == r.sha256
        assert cli["base_url"] == gateway.config.base_url == cfg.llm_base_url
        assert "LLM_API_KEY" not in out and not cli["live_authorized"]
        # A file update cannot mutate already resolved models, including follow-up stages.
        path.write_text(load_runtime().model_dump_json())
        assert gateway.runtime.stages.verify.model_id == GLM
        queued = r.snapshot("adaptive")
        assert pinned_runtime(queued)[0].stages.verify.model_id == GLM
        with pytest.raises(AppError):
            pinned_runtime(None)
    finally:
        await db.dispose()


async def test_live_preflight_checks_all_tariffs_and_output_cap_without_http(tmp_path):
    db = create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")
    cfg = Settings(
        analysis_mode="gateway", live_llm_enabled=True, llm_api_key=SecretStr("offline-test-only")
    )
    try:
        g = create_gateway(db, cfg, runtime=mixed_runtime(), mode="two_call_smoke")
        with pytest.raises(AppError, match="Billable output"):
            authorize_live_g1(g, Decimal("5"))
        missing = tmp_path / "tariffs.json"
        missing.write_text('{"tariffs": []}')
        with pytest.raises(AppError) as err:
            create_gateway(db, cfg.model_copy(update={"llm_tariff_file": str(missing)}))
        assert err.value.code == ErrorCode.budget_unknown
    finally:
        await db.dispose()


async def test_mixed_engine_models_pinned_tokens_and_honest_aggregate():
    class Recording(Planning):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.requests = []

        async def complete(self, req, schema):
            self.requests.append(req)
            return await super().complete(req, schema)

    llm = Recording()
    raw = mixed_runtime().model_dump()
    for stage, cap in zip(("plan", "draft", "verify"), (211, 377, 199), strict=True):
        raw["stages"][stage]["max_output_tokens"] = cap
    r = RuntimeConfig.model_validate(raw)
    e, _, _, ctx, req = setup(baseline_data.__wrapped__(), llm=llm, runtime=r)
    a = await e.analyze(ctx, req)
    assert [(x.model_id, x.max_output_tokens) for x in llm.requests] == [
        (GEMMA, 211),
        (DEEPSEEK, 377),
        (GLM, 199),
    ]
    assert a.model_id is None and a.usage_status == "provider"
    assert any(r.sha256 in x for x in e.checkpoints)
    assert any("разные модели" in n.text for n in a.notices)
    # Failure attribution is the attempted stage, never an unrelated primary default.
    llm = Recording(fail="plan")
    e, _, _, ctx, req = setup(baseline_data.__wrapped__(), llm=llm, runtime=r)
    a = await e.analyze(ctx, req)
    assert a.model_id == GEMMA and a.usage_status == "unknown"
    assert llm.calls == ["plan"]


async def test_g1_pins_models_limits_tariffs_config_and_endpoint():
    cfg = Settings(analysis_mode="controlled", demo_mode=True)
    db = create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test")
    try:
        r = mixed_runtime()
        g = create_gateway(db, cfg, runtime=r, mode="two_call_smoke")
        auth = authorize_live_g1(g, Decimal("5"))
        assert auth.stages.draft.model_id == DEEPSEEK and auth.stages.verify.model_id == GLM
        assert auth.runtime.sha256 == r.sha256
        LiveG1Client(g, "job", auth)
        changed = r.model_dump()
        changed["stages"]["verify"]["model_id"] = GEMMA
        other = create_gateway(
            db, cfg, runtime=RuntimeConfig.model_validate(changed), mode="two_call_smoke"
        )
        with pytest.raises(AppError):
            LiveG1Client(other, "job", auth)
        # Even a budget-preserving output limit change cannot silently alter an authorization.
        changed = r.model_dump()
        changed["stages"]["draft"]["max_output_tokens"] = 899
        other = create_gateway(
            db, cfg, runtime=RuntimeConfig.model_validate(changed), mode="two_call_smoke"
        )
        with pytest.raises(AppError):
            LiveG1Client(other, "job", auth)
    finally:
        await db.dispose()


async def test_config_can_disable_refinement_without_disabling_initial_search():
    raw = load_runtime().model_dump()
    raw["limits"]["repeated_searches"] = 0
    e, _, tools, ctx, req = setup(
        baseline_data.__wrapped__(), runtime=RuntimeConfig.model_validate(raw)
    )
    await e.analyze(ctx, req)
    assert tools.queries == ["initial"]
    assert not any(x.startswith("refined_search:") for x in e.checkpoints)
