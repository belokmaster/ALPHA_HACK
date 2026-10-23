"""Full-size acceptance probe against an explicitly isolated, migrated alfa_test DB.

Run three fresh processes, in order: --scenario failure, apply, repeat.
This script never drops data, never seeds grants, and rejects a nonempty database
before the failure/apply scenarios. CSV files are read only.
"""

import argparse
import asyncio
import json
import os
import resource
import time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.ingestion.csv.audit import audited_inputs, require_importable
from app.ingestion.csv.importer import PAYMENT_ROWS_SQL, import_snapshot

TABLES = (t.source_versions, t.companies, t.features, t.relations, t.quality_issues, t.grants)


class InjectedFailure(Exception):
    pass


async def counts(conn):
    return {
        table.name: (await conn.execute(select(func.count()).select_from(table))).scalar_one()
        for table in TABLES
    }


def rejection_counts(index, report):
    # Exclusive categories: exact raw duplicates first, then distinct pair variants,
    # then other invalid rows. Never SUM payments or reinterpret noisy pairs.
    ambiguous = index.execute(
        "SELECT COUNT(DISTINCT p.row_hash) FROM payment_pairs k CROSS JOIN payments p "
        "ON p.PAYER=k.PAYER AND p.BENEFICIARY=k.BENEFICIARY WHERE k.versions>1"
    ).fetchone()[0]
    invalid = index.execute(
        "SELECT COUNT(*) FROM payment_pairs k CROSS JOIN payments p ON p.row_no=k.first_row "
        "WHERE k.versions=1 AND p.valid=0"
    ).fetchone()[0]
    payments = report["files"]["payments"]
    return {
        "payment_exact_duplicates": payments["exact_duplicate_rows"],
        "payment_ambiguous_distinct_rows": ambiguous,
        "payment_other_invalid": invalid,
        "payment_rows_loaded": payments["normalized_diagnostics"]["rows"],
        "company_quarantined_rows": report["files"]["companies"]["quarantined_rows"],
        "company_quarantined_ids": report["files"]["companies"]["quarantined_ids"],
        "link_exact_duplicates": report["files"]["links"]["exact_duplicate_rows"],
    }


async def verify(args):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.endswith("/alfa_test") or os.environ.get("CASE_IMPORT_VERIFY_ISOLATED") != "1":
        raise ValueError(
            "Explicit isolated TEST_DATABASE_URL /alfa_test and CASE_IMPORT_VERIFY_ISOLATED=1 required"
        )
    engine = create_async_engine(url)
    started = time.monotonic()
    result = {"scenario": args.scenario, "status": "failed"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result) + "\n")
    try:
        async with engine.connect() as observer:
            assert (
                await observer.execute(text("SELECT current_database()"))
            ).scalar_one() == "alfa_test"
            before = await counts(observer)
        result["before"] = before
        if args.scenario != "repeat" and any(before.values()):
            raise ValueError("Nonempty DB: create a new isolated DB for failure/apply")
        with audited_inputs(args.input_dir) as (index, report):
            require_importable(report)
            result["audit"] = report
            result["rejections"] = rejection_counts(index, report)
            result["payment_query_plan"] = [
                tuple(row) for row in index.execute("EXPLAIN QUERY PLAN " + PAYMENT_ROWS_SQL)
            ]
            print(
                json.dumps({"phase": "audit", "elapsed_s": time.monotonic() - started}), flush=True
            )
            target = 2 * report["files"]["payments"]["normalized_diagnostics"]["rows"] - 5000
            last = {}

            async def checkpoint(table, count):
                if table not in last or count - last[table] >= 250_000:
                    last[table] = count
                    print(
                        json.dumps(
                            {
                                "phase": table,
                                "rows": count,
                                "elapsed_s": round(time.monotonic() - started, 3),
                            }
                        ),
                        flush=True,
                    )
                if args.scenario == "failure" and table == "features" and count >= target:
                    # The writer has almost the entire dataset. A different connection
                    # must still see exactly the pre-import snapshot of *every* table.
                    async with engine.connect() as observer:
                        visible = await counts(observer)
                    result["visible_before_injected_failure"] = visible
                    assert visible == before
                    result["failure_after_features"] = count
                    raise InjectedFailure("Late failure after COPY, before atomic publication")

            transaction_started = time.monotonic()
            try:
                async with engine.begin() as conn:
                    imported = await import_snapshot(conn, index, report, checkpoint=checkpoint)
            except InjectedFailure:
                if args.scenario != "failure":
                    raise
                imported = {"status": "rolled_back"}
            result["transaction_seconds"] = round(time.monotonic() - transaction_started, 3)
            async with engine.connect() as observer:
                after = await counts(observer)
                result["database_bytes"] = (
                    await observer.execute(text("SELECT pg_database_size(current_database())"))
                ).scalar_one()
            result["after"] = after
            if args.scenario == "failure":
                assert imported["status"] == "rolled_back" and after == before
            elif args.scenario == "repeat":
                assert imported["status"] == "already_imported" and after == before
            else:
                assert imported["status"] == "imported" and after["source_versions"] == 3
                assert after["grants"] == 0
                assert after["features"] == 2 * result["rejections"]["payment_rows_loaded"]
                assert after["relations"] == imported["inserted"]["relations"]
                async with engine.connect() as observer:
                    # Exhaustive financial totals across every persisted payment
                    # feature, checked against independently measured source audit.
                    totals = (
                        (
                            await observer.execute(
                                text("""
                        SELECT payload->>'name' AS name,
                               SUM((payload->>'value')::numeric) AS total
                        FROM features GROUP BY payload->>'name'
                    """)
                            )
                        )
                        .mappings()
                        .all()
                    )
                    totals = {row["name"]: str(row["total"]) for row in totals}
                    expected = report["files"]["payments"]["normalized_diagnostics"]
                    assert Decimal(totals["PAY_AMT"]) == Decimal(expected["amount_rub"])
                    assert Decimal(totals["PAY_CNT"]) == expected["payment_count"]
                    statuses = dict(
                        (
                            await observer.execute(
                                text("""
                        SELECT payload->>'status', COUNT(*) FROM company_snapshots
                        GROUP BY payload->>'status'
                    """)
                            )
                        ).all()
                    )
                    kinds = dict(
                        (
                            await observer.execute(
                                text("SELECT kind, COUNT(*) FROM relations GROUP BY kind")
                            )
                        ).all()
                    )
                    assert (
                        statuses["quarantined"] == result["rejections"]["company_quarantined_ids"]
                    )
                    assert (
                        after["quality_issues"] == result["rejections"]["company_quarantined_rows"]
                    )
                    assert kinds["self_transfer"] == expected["self_transfer"]["rows"]
                result["persisted_payment_totals"] = totals
                result["profile_statuses"] = statuses
                result["relation_kinds"] = kinds
            result.update(status="passed", import_result=imported)
    finally:
        await engine.dispose()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "scenario",
                    "status",
                    "elapsed_seconds",
                    "process_peak_rss_kib",
                    "after",
                )
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", choices=["failure", "apply", "repeat"], required=True)
    asyncio.run(verify(parser.parse_args()))
