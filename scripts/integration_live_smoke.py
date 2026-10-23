"""Explicit, bounded paid G1 job through the existing worker and shared gateway.

Preparation never calls this. Use only after owner authorization, verified tariffs,
one shared ledger DB for the key, configured gateway worker and local data import.
"""

import argparse
import asyncio
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.service import authorize, current_context
from app.core import tables as t
from app.core.config import Settings
from app.jobs.service import enqueue, replay
from app.llm.factory import create_gateway
from app.llm.g1_live import POLICY, authorize_live_g1
from app.llm.pinning import live_pin
from contracts.v1 import Analysis, AnalysisRequest


async def queue_live_g1(c, ctx, request, profile, authorization, request_id):
    """One durable idempotent job; its only permitted call IDs derive from this job."""
    raw = request.model_dump(mode="json")
    body = {"request": raw, "authorization": authorization.model_dump(mode="json")}
    _, digest = await replay(c, ctx, "operator/integration-smoke", request_id, body)
    return await enqueue(
        c,
        ctx,
        "operator/integration-smoke",
        request_id,
        body,
        {
            "runner": "analysis_engine",
            "policy_version": POLICY,
            "live_g1_authorization": authorization.model_dump(mode="json"),
            "required_worker_mode": "gateway",
            "llm_runtime": authorization.runtime.model_dump(mode="json"),
            "llm_live_pin": live_pin(Settings(), authorization.runtime.config),
            "context": ctx.model_dump(mode="json"),
            "request": raw,
            "company_snapshot_id": profile.snapshot_id,
        },
        "analysis",
        digest,
    )


async def smoke(args):
    settings = Settings()
    if settings.analysis_mode != "gateway" or not settings.live_llm_enabled:
        raise SystemExit("Requires ANALYSIS_MODE=gateway and explicit LIVE_LLM_ENABLED=true")
    engine = create_async_engine(settings.database_url)
    try:
        gateway = create_gateway(engine, settings, mode="two_call_smoke")
        models = {p: gateway.runtime.stage(p).model_id for p in ("draft", "verify")}
        authorization = authorize_live_g1(gateway, args.budget_rub)
        maximum = authorization.maximum_reserve_rub
        async with engine.begin() as c:
            ctx = await current_context(c, args.actor, lock=True)
            if ctx.company_id != args.company:
                raise SystemExit("Select the authorized company in current server context first")
            profile = await authorize(c, ctx, args.company, "analysis.run")
            request = AnalysisRequest(
                event_id=args.event, context_version=ctx.version, as_of=args.as_of
            )
            event = (
                await c.execute(select(t.events.c.payload).where(t.events.c.id == request.event_id))
            ).scalar_one()
            if event["domain"] != "case":
                raise SystemExit("This smoke is for an explicit historical case, not live news")
            job = await queue_live_g1(c, ctx, request, profile, authorization, args.request_id)
        print(
            json.dumps(
                {
                    "job_id": job.id,
                    "models": models,
                    "runtime_sha256": gateway.runtime.sha256,
                    "base_url": gateway.config.base_url,
                    "planning": "code_two_call_cap",
                    "max_calls": args.max_calls,
                    "maximum_reserve_rub": str(maximum),
                    "approved_cap_rub": str(args.budget_rub),
                }
            ),
            flush=True,
        )
        for _ in range(140):
            async with engine.connect() as c:
                row = (
                    (await c.execute(select(t.jobs).where(t.jobs.c.id == job.id))).mappings().one()
                )
                if row["result_id"]:
                    data = (
                        await c.execute(
                            select(t.analyses.c.payload).where(t.analyses.c.id == row["result_id"])
                        )
                    ).scalar_one()
                    result = Analysis.model_validate(data)
                    print(
                        json.dumps(
                            {
                                "analysis_id": result.id,
                                "delivery": result.delivery.mode,
                                "usage_status": result.usage_status,
                                "status": result.status,
                                "url": "/?analysis=" + result.id,
                            },
                            ensure_ascii=False,
                        )
                    )
                    return
                if row["status"] == "failed":
                    print(json.dumps(row["error"], ensure_ascii=False))
                    raise SystemExit(1)
            await asyncio.sleep(0.5)
        raise SystemExit("Job still pending. Inspect saved job; do not blindly repeat a paid call")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--actor", choices=["alice", "bob"], required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--event", default="case-rate-20260911")
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--request-id",
        required=True,
        help="Stable idempotency key; reuse to inspect this job, never invent a key to retry uncertain",
    )
    parser.add_argument("--max-calls", type=int, choices=[2], default=2)
    parser.add_argument("--budget-rub", type=Decimal, default=Decimal("5"))
    args = parser.parse_args()
    if (
        not args.allow_paid
        or not 1 <= len(args.request_id) <= 128
        or not args.budget_rub.is_finite()
        or not Decimal("0") < args.budget_rub <= Decimal("5")
    ):
        parser.error("Explicit --allow-paid and finite 0 < budget-rub <= 5 required")
    asyncio.run(smoke(args))


if __name__ == "__main__":
    main()
