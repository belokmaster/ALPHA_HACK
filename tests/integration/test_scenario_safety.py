"""Scenario cleanup must refuse a normal test DB without the disposable marker."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


async def test_scenario_restoration_cannot_touch_unmarked_shared_database(db, monkeypatch):
    monkeypatch.setenv("ANALYSIS_MODE", "controlled")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LIVE_LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("CONTROLLED_LLM_RESPONSE", "ok")
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scenarios/isolation.py"))
    async with db.connect() as c:
        before = await module["snapshot"](c)
    with pytest.raises(RuntimeError, match="marker missing"):
        async with module["isolated_scenario"](db):
            pytest.fail("Must refuse before yielding or restoring any data")
    async with db.connect() as c:
        assert await module["snapshot"](c) == before
