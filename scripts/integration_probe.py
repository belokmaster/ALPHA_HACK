"""Real-data G1 + FTS/exact/HNSW diagnostic. Controlled transport only; no paid calls."""

import asyncio
import json
import os
import resource
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.retrieval.service import HybridRetriever, LocalE5Encoder
from contracts.v1 import Analysis, AnalysisRequest, UserContext


async def probe():
    cfg = Settings()
    if (
        cfg.analysis_mode != "controlled"
        or cfg.live_llm_enabled
        or cfg.llm_api_key.get_secret_value()
    ):
        raise SystemExit("Explicit controlled mode, no live/key required")
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    db = create_async_engine(url)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            base_url=os.environ.get("TEST_BASE_URL", "http://app:8000"), timeout=15
        ) as client:
            response = await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
            response.raise_for_status()
            session = response.json()
            client.headers["X-CSRF-Token"] = session["csrf_token"]
            response = await client.patch(
                "/api/v1/me/context",
                json={
                    "expected_version": session["context"]["version"],
                    "company_id": os.environ.get("INTEGRATION_COMPANY", "U_15728"),
                    "role": "cfo",
                },
            )
            response.raise_for_status()
            ctx = UserContext.model_validate(response.json())
            req = AnalysisRequest(
                event_id="case-rate-20260911",
                context_version=ctx.version,
                as_of="2026-09-14T00:00:00Z",
            )
            encoder = LocalE5Encoder()
            assert encoder.is_available, "Real pinned E5 required for this diagnostic"
            retriever = HybridRetriever(db, encoder)
            comparisons = {}
            for mode in ("lexical", "exact", "hnsw"):
                begin = time.perf_counter()
                result = await retriever.search_with_meta(
                    ctx,
                    req,
                    "ключевая ставка прогноз конец года",
                    mode_preference=mode,
                    explain=True,
                )
                comparisons[mode] = {
                    "seconds": time.perf_counter() - begin,
                    "mode": result.retrieval_mode,
                    "evidence_ids": [e.id for e in result.evidence],
                    "plan": result.query_plan,
                    "hnsw_index_used": "idx_evidence_hnsw" in json.dumps(result.query_plan),
                    "forced_ann": mode == "hnsw",
                    "financial_quality_metric": False,
                }
            assert comparisons["hnsw"]["hnsw_index_used"]
            response = await client.post(
                "/api/v1/analyses",
                headers={"Idempotency-Key": uuid4().hex},
                json=req.model_dump(mode="json"),
            )
            assert response.status_code == 202, response.text
            job = response.json()
            for _ in range(300):
                state = (await client.get(job["poll_url"])).json()
                if state["status"] == "failed":
                    raise AssertionError(state)
                if state["result_id"]:
                    response = await client.get("/api/v1/analyses/" + state["result_id"])
                    response.raise_for_status()
                    analysis = Analysis.model_validate(response.json())
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("Worker timeout")
            assert analysis.delivery.original_mode == "controlled"
            assert any(
                "13,5%" in e.quote and "2026" in e.quote and "14,0%" in e.quote
                for e in analysis.evidence
            )
            assert any("2027" in e.quote and "12,0%" in e.quote for e in analysis.evidence)
            for e in analysis.evidence:
                assert (await client.get("/evidence/" + e.id)).status_code == 200
            news = (await client.get("/api/v1/news")).json()
            assert {n["domain"] for n in news} == {"live"} and all(
                n["id"] != analysis.event_id for n in news
            )
            for event in news:
                for eid in event["evidence_ids"]:
                    ev_response = await client.get("/api/v1/evidence/" + eid)
                    ev_response.raise_for_status()
                    assert (await client.get("/evidence/" + eid)).status_code == 200
            report = {
                "company": ctx.company_id,
                "analysis_id": analysis.id,
                "url": "/?analysis=" + analysis.id,
                "seconds": time.perf_counter() - started,
                "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "delivery": analysis.delivery.model_dump(mode="json"),
                "usage_status": analysis.usage_status,
                "financial_quality_proven": False,
                "bank_evidence_status": analysis.bank_evidence_status,
                "real_live_news": len(news),
                "retrieval": comparisons,
            }
            out = Path("artifacts/integration")
            out.mkdir(parents=True, exist_ok=True)
            (out / "g1-final-analysis.json").write_text(analysis.model_dump_json(indent=2))
            (out / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k != "retrieval"}, ensure_ascii=False
                )
            )
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(probe())
