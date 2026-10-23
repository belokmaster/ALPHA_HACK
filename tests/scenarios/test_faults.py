"""One explicit test per worker transport mode; timeout is last and leaves gate held."""

import os

from sqlalchemy import select
from test_g1 import finish, login, start

from app.core import tables as t
from app.llm.tables import key_gates


async def test_gateway_stop_is_persisted_without_blind_retry(db):
    response_mode = os.environ.get("CONTROLLED_LLM_RESPONSE")
    expected = {
        "invalid_json": "llm_invalid_response",
        "budget": "budget_exceeded",
        "timeout": "llm_outcome_unknown",
    }[response_mode]
    client, ctx = await login()
    try:
        result = await finish(client, await start(client, ctx))
        assert result.delivery.original_mode == "evidence_only"
        assert any(expected in n.text for n in result.notices)
        assert not [c for c in result.claims if c.origin == "model"]
        assert (
            result.usage_status
            == {"invalid_json": "provider", "budget": "not_called", "timeout": "unknown"}[
                response_mode
            ]
        )
        async with db.connect() as c:
            job = (
                (await c.execute(select(t.jobs).where(t.jobs.c.id == result.job_id)))
                .mappings()
                .one()
            )
            calls = (
                (
                    await c.execute(
                        select(t.metadata.tables["model_calls"]).where(
                            t.metadata.tables["model_calls"].c.job_id == job["id"]
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert job["attempt"] == 1 and len(calls) == (0 if response_mode == "budget" else 1)
            if response_mode == "timeout":
                assert calls[0]["outcome"] == "uncertain"
                gate = (
                    await c.execute(
                        select(key_gates.c.active_call_id).where(
                            key_gates.c.active_call_id == calls[0]["id"]
                        )
                    )
                ).scalar_one()
                assert gate == calls[0]["id"]
        if response_mode == "timeout":
            # A new job cannot evade the previous uncertain call with a fresh ID.
            later = await finish(client, await start(client, ctx))
            assert any("llm_busy" in n.text for n in later.notices)
            assert later.usage_status == "not_called"
    finally:
        await client.aclose()
