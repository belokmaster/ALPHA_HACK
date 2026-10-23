"""Explicit controlled demo probe/export; never usable for paid/live calls.

Run in the marked disposable integration stack after prepare_product. This creates
saved demo analyses through API/worker, not UI fixtures. Output path must be writable.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings


async def probe(output):
    cfg = Settings()
    if (
        cfg.analysis_mode != "controlled"
        or cfg.live_llm_enabled
        or cfg.llm_api_key.get_secret_value()
    ):
        raise ValueError("Controlled mode, no live/key required")
    db = create_async_engine(cfg.database_url)
    try:
        async with db.connect() as c:
            marker = await c.scalar(
                text(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
                )
            )
            if db.url.database != "alfa_test" or marker != "g1-disposable-offline-scenarios-v1":
                raise ValueError("Disposable offline integration database required")
    finally:
        await db.dispose()
    report = {
        "transport": "controlled templates, not real LLM generalization",
        "as_of": "2026-10-22T12:00:00Z",
        "feeds": {},
        "analyses": {},
    }
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_BASE_URL", "http://app:8000"), timeout=30
    ) as client:
        response = await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
        response.raise_for_status()
        ctx = response.json()["context"]
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

        async def switch(company, role):
            nonlocal ctx
            r = await client.patch(
                "/api/v1/me/context",
                json={"expected_version": ctx["version"], "company_id": company, "role": role},
            )
            r.raise_for_status()
            ctx = r.json()

        async def run(name, event_id):
            r = await client.post(
                "/api/v1/analyses",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "event_id": event_id,
                    "context_version": ctx["version"],
                    "as_of": report["as_of"],
                },
            )
            r.raise_for_status()
            job = r.json()
            for _ in range(300):
                r = await client.get(job["poll_url"])
                r.raise_for_status()
                job = r.json()
                if job["status"] == "failed":
                    raise RuntimeError(job)
                if job.get("result_id"):
                    r = await client.get("/api/v1/analyses/" + job["result_id"])
                    r.raise_for_status()
                    report["analyses"][name] = r.json()
                    return
                await asyncio.sleep(0.2)
            raise TimeoutError("Separate worker did not complete")

        for company in ["U_15728", "U_11496"]:
            await switch(company, "cfo")
            r = await client.get("/api/v1/feed", params={"as_of": report["as_of"]})
            r.raise_for_status()
            report["feeds"][company] = r.json()
        await switch("U_15728", "cfo")
        await run("g1_cfo", "case-rate-20260911")
        await run("unseen_rates", "case-bank-rate-outlook-2027")
        fuel = next(
            x["event"] for x in report["feeds"]["U_15728"] if x["event"]["domain"] == "live"
        )
        await run("rosstat_fuel", fuel["id"])
        await switch("U_15728", "ceo")
        await run("g1_ceo", "case-rate-20260911")
        await switch("U_15728", "cfo")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "saved": {k: a["id"] for k, a in report["analyses"].items()},
                "paid_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
