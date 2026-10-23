"""Real inputs/API/separate fixture worker with controlled HTTP, never paid live."""

import os
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from test_g1 import COMPANY, EVENT, finish, login

from app.auth.service import authorize
from app.budgets.tables import model_calls
from app.core.config import Settings
from app.llm.factory import create_gateway
from app.llm.g1_live import authorize_live_g1
from app.llm.tables import key_gates, llm_runs
from contracts.v1 import AnalysisRequest, UserContext
from scripts.integration_live_smoke import queue_live_g1


async def test_operator_job_with_real_inputs_and_separate_offline_policy_worker(db):
    mode = os.environ["CONTROLLED_LLM_RESPONSE"]
    assert mode in {"ok", "invalid_json", "timeout"}
    cfg = Settings()
    assert cfg.analysis_mode == "controlled" and not cfg.live_llm_enabled
    gateway = create_gateway(db, cfg, mode="two_call_smoke")
    authorization = authorize_live_g1(gateway, Decimal("5"))
    client, raw_context = await login()
    try:
        ctx = UserContext.model_validate(raw_context)
        req = AnalysisRequest(
            event_id=EVENT, context_version=ctx.version, as_of="2026-09-14T00:00:00Z"
        )
        key = "offline-operator-" + mode + "-" + uuid4().hex
        async with db.begin() as c:
            profile = await authorize(c, ctx, COMPANY, "analysis.run")
            job = await queue_live_g1(c, ctx, req, profile, authorization, key)
        result = await finish(client, job)
        assert result.company_id == COMPANY and result.domain == "case"
        if mode == "ok":
            assert any(c.origin == "model" for c in result.claims)
        else:
            expected = "llm_outcome_unknown" if mode == "timeout" else "llm_invalid_response"
            assert any(expected in n.text for n in result.notices)
            assert not any(c.origin == "model" for c in result.claims)
        async with db.begin() as c:
            replayed = await queue_live_g1(c, ctx, req, profile, authorization, key)
            assert replayed.id == job.id
            calls = (
                (await c.execute(select(model_calls).where(model_calls.c.job_id == job.id)))
                .mappings()
                .all()
            )
            assert len(calls) == (2 if mode == "ok" else 1)
            assert {r["id"] for r in calls} <= {job.id + "_draft", job.id + "_verify"}
            row = (
                (await c.execute(select(llm_runs).where(llm_runs.c.job_id == job.id)))
                .mappings()
                .one()
            )
            assert row["quoted_rub"] <= authorization.maximum_reserve_rub <= 5
            assert row["retry_used"] == row["repair_used"] == 0
            if mode == "timeout":
                assert calls[0]["outcome"] == "uncertain"
                assert (
                    await c.scalar(
                        select(key_gates.c.active_call_id).where(
                            key_gates.c.scope_id == gateway.scope
                        )
                    )
                    == job.id + "_draft"
                )
        print(
            f"offline_policy_worker: mode={mode}; actual controlled calls={len(calls)}; replay_job={replayed.id}; paid_calls=0"
        )
    finally:
        await client.aclose()
