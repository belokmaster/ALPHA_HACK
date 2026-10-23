"""Export an explicitly selected saved controlled G1; never runs the analysis/tests.

Only isolated alfa_test, SELECT in a read-only repeatable-read transaction. Output
directory is mandatory and must not exist. Existing review packets are untouched.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from contracts.v1 import Analysis


async def read_saved(engine, analysis_id, followup_id=None):
    async with engine.connect() as c:
        await c.begin()
        await c.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

        async def analysis(identifier):
            raw = await c.scalar(select(t.analyses.c.payload).where(t.analyses.c.id == identifier))
            if raw is None:
                raise ValueError("Saved analysis not found: " + identifier)
            result = Analysis.model_validate(raw)
            if result.delivery.mode != "controlled":
                raise ValueError("This exporter only accepts controlled results")
            return raw

        base = await analysis(analysis_id)
        payload = {"analysis.json": base}
        if followup_id:
            follow = await analysis(followup_id)
            if follow["parent_analysis_id"] != base["id"]:
                raise ValueError("Follow-up belongs to another saved analysis")
            payload["followup.json"] = follow
        trace = await c.scalar(select(t.runs.c.payload).where(t.runs.c.id == base["run_id"]))
        if trace is None:
            raise ValueError("Saved trace missing")
        payload["trace.json"] = trace
        await c.rollback()
    return payload


def write_export(output, payload):
    # Never use a default/implicit artifact path or overwrite a previous review.
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, value in payload.items():
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        (output / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {
        "classification": "controlled_saved_export",
        "human_decisions": 0,
        "analysis_id": payload["analysis.json"]["id"],
        "files": hashes,
    }
    (output / "export.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


async def export(args):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise ValueError("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    if args.output_dir.exists():
        raise ValueError("Output directory exists; refusing overwrite")
    engine = create_async_engine(url)
    try:
        payload = await read_saved(engine, args.analysis, args.followup)
    finally:
        await engine.dispose()
    print(json.dumps(write_export(args.output_dir, payload), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--followup")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(export(args))
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
