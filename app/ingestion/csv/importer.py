"""Explicit, atomic case import from the audited on-disk snapshot; never grants access.

The current schema has one snapshot per company. A different dataset must fail
closed instead of silently replacing a profile referenced by grants or analyses.
"""

import argparse
import asyncio
import hashlib
import json
import re
import resource
import sqlite3
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core import tables as t
from app.ingestion.csv.audit import SCHEMAS, audited_inputs, require_importable
from app.ingestion.csv.normalization import (
    FLAG_FIELDS,
    PAYMENT_PERIOD,
    PAYMENT_SEMANTIC_SOURCE,
    RULE_VERSION,
    NormalizedValue,
    normalize_client_type,
    normalize_flag,
    normalize_fts_countries,
    normalize_owner_information,
    normalize_revenue,
)
from contracts.v1 import (
    SCHEMA_VERSION,
    CompanyProfile,
    DataQualityIssue,
    GroupContext,
    Quality,
    RelationPath,
    SourceRef,
    SourceVersion,
)

IMPORT_VERSION = "case-csv-2"
# Fix traversal independently of SQLite's automatic-index estimates. The explicit
# INTEGER first_row affinity in audit is also required for indexed equality joins.
PAYMENT_ROWS_SQL = (
    "SELECT p.* FROM payment_pairs k CROSS JOIN payments p ON p.row_no=k.first_row "
    "WHERE k.versions=1 AND p.valid=1 ORDER BY k.first_row"
)
COPY_BATCH_ROWS = 5000


def stable_id(prefix, *parts):
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()
    return f"{prefix}_{digest}"


def valid_id(value):
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value):
        raise ValueError("Invalid source identifier; no automatic trimming or identity inference")
    return value


def source_versions(report):
    result = {}
    for kind, item in report["files"].items():
        sid = stable_id("csv", IMPORT_VERSION, kind, item["sha256"])
        ref = SourceRef(
            source_version_id=sid,
            original_filename=item["filename"],
            sha256=item["sha256"],
            parser_version="csv-audit-1",
            rule_version=RULE_VERSION,
            semantic_source_ref=PAYMENT_SEMANTIC_SOURCE if kind == "payments" else None,
        )
        result[kind] = SourceVersion(
            id=sid,
            ref=ref,
            domain="case",
            import_version=IMPORT_VERSION,
            period=PAYMENT_PERIOD if kind == "payments" else None,
        )
    return result


def row_ref(source, row, kind):
    # A source span retains all raw cells, including quarantined rows and link geography.
    return source.ref.model_copy(
        update={
            "row": row["row_no"],
            "span": json.dumps({key: row[key] for key in SCHEMAS[kind]}, ensure_ascii=False),
        }
    )


def company_profile(row, source, quarantined):
    cid = valid_id(row["SYNTHETIC_U"])
    snapshot_id = stable_id("snapshot", source.id, cid)
    ref = row_ref(source, row, "companies")
    features = []
    if not quarantined:
        for name in SCHEMAS["companies"]:
            raw = row[name]
            if name == "REVENUE_COMPANY":
                value = normalize_revenue(raw)
            elif name == "NEGATIVEMARK":
                value = normalize_owner_information(raw)
            elif name in FLAG_FIELDS:
                value = normalize_flag(raw)
            elif name == "FTS_COUNTRY_NAME":
                value = normalize_fts_countries(raw)
            elif name == "CL_TYPE":
                value = normalize_client_type(raw)
            else:
                value = NormalizedValue(
                    raw,
                    raw if raw.strip() else None,
                    Quality.unverified if raw.strip() else Quality.unknown,
                    ("source_value_not_independently_verified",),
                )
            # Raw cell is on Feature; avoid duplicating the full row on every feature.
            feature_ref = ref.model_copy(update={"span": None})
            features.append(
                value.to_feature(stable_id("feature", snapshot_id, name), name, feature_ref)
            )
    group = row["GROUP_ID"] if not quarantined else ""
    return CompanyProfile(
        company_id=cid,
        snapshot_id=snapshot_id,
        source_version_id=source.id,
        name=cid,
        industry=row["MAINOKVED_CODE"] or None if not quarantined else None,
        client_type=normalize_client_type(row["CL_TYPE"]).value if not quarantined else "unknown",
        status="quarantined" if quarantined else "valid",
        features=features,
        group=GroupContext(group_id=valid_id(group) if group else None),
        warnings=["availability_unknown", "snapshot_not_historical_backtest"]
        + (["conflicting_company_id"] if quarantined else []),
    )


async def import_snapshot(connection, index, report, *, checkpoint=None):
    require_importable(report)
    sources = source_versions(report)
    counts = Counter()
    # Serialize explicit imports. No runtime/model calls and no database privileges inferred from CSV.
    await connection.execute(text("SELECT pg_advisory_xact_lock(4202602)"))
    existing = set(
        (
            await connection.execute(
                select(t.source_versions.c.id).where(
                    t.source_versions.c.import_version.like("case-csv-%")
                )
            )
        ).scalars()
    )
    expected = {s.id for s in sources.values()}
    if existing:
        if existing == expected:
            return {"status": "already_imported", "source_version_ids": sorted(expected)}
        raise ValueError(
            "Changed case dataset: versioned company migration required; nothing replaced"
        )

    pending = {}
    driver = (await connection.get_raw_connection()).driver_connection

    async def copy_batch(table, batch):
        columns = tuple(batch[0])
        records = [
            tuple(
                json.dumps(row[col], ensure_ascii=False, separators=(",", ":"))
                if col == "payload"
                else row[col]
                for col in columns
            )
            for row in batch
        ]
        # Uses the *same* asyncpg connection and transaction as the source marker
        # and foreign keys. COPY does not commit or publish a partial dataset.
        await driver.copy_records_to_table(table.name, records=records, columns=columns)
        if checkpoint is not None:
            await checkpoint(table.name, counts[table.name])
        batch.clear()

    async def add(table, values):
        batch = pending.setdefault(table, [])
        batch.append(values)
        counts[table.name] += 1
        # Profiles contain 24 nested features. Keep their byte footprint bounded too.
        if len(batch) >= (250 if table is t.companies else COPY_BATCH_ROWS):
            await copy_batch(table, batch)

    async def flush():
        for table, batch in pending.items():
            if batch:
                await copy_batch(table, batch)

    for source in sources.values():
        await connection.execute(
            insert(t.source_versions).values(
                id=source.id,
                sha256=source.ref.sha256,
                import_version=IMPORT_VERSION,
                payload=source.model_dump(mode="json"),
            )
        )
    index.row_factory = sqlite3.Row
    quarantine = {q["company_id"] for q in report["files"]["companies"]["quarantine"]}
    snapshots = {}
    for row in index.execute("SELECT * FROM companies GROUP BY SYNTHETIC_U ORDER BY SYNTHETIC_U"):
        profile = company_profile(row, sources["companies"], row["SYNTHETIC_U"] in quarantine)
        snapshots[profile.company_id] = profile.snapshot_id
        await add(
            t.companies,
            dict(
                id=profile.snapshot_id,
                company_id=profile.company_id,
                source_id=profile.source_version_id,
                payload=profile.model_dump(mode="json"),
            ),
        )
    await flush()
    # Keep every conflicting row traceable, not a last-row-wins company.
    for cid in sorted(quarantine):
        for row in index.execute("SELECT * FROM companies WHERE SYNTHETIC_U=?", (cid,)):
            issue = DataQualityIssue(
                id=stable_id("issue", sources["companies"].id, row["row_no"]),
                source=row_ref(sources["companies"], row, "companies"),
                code="conflicting_company_id",
                severity="error",
                safe_behavior="quarantine_all_versions",
            )
            await add(
                t.quality_issues,
                dict(
                    id=issue.id,
                    source_id=sources["companies"].id,
                    payload=issue.model_dump(mode="json"),
                ),
            )
    # Stub nodes never become eligible profiles and never receive automatic grants.
    nodes = index.execute(
        "SELECT id, 1 AS payment_node FROM payment_nodes UNION ALL "
        "SELECT DISTINCT SYNTHETIC_U, 0 FROM links WHERE SYNTHETIC_U NOT IN "
        "(SELECT id FROM payment_nodes)"
    )
    for row in nodes:
        cid = valid_id(row[0])
        if cid in snapshots:
            continue
        sid = stable_id("stub", sources["payments"].id, sources["links"].id, cid)
        snapshots[cid] = sid
        profile = CompanyProfile(
            company_id=cid,
            snapshot_id=sid,
            source_version_id=sources["payments" if row[1] else "links"].id,
            name=cid,
            industry=None,
            client_type="unknown",
            status="stub",
            features=[],
            warnings=["unknown_counterparty", "no_inferred_identity"],
        )
        await add(
            t.companies,
            dict(
                id=sid,
                company_id=cid,
                source_id=profile.source_version_id,
                payload=profile.model_dump(mode="json"),
            ),
        )
    await flush()

    async def relation(kind, nodes, source, ref, quality="verified"):
        rel = RelationPath(
            id=stable_id("relation", source.id, kind, nodes, ref.row),
            kind=kind,
            nodes=nodes,
            provenance=[ref],
            quality=quality,
        )
        await add(
            t.relations,
            dict(id=rel.id, source_id=source.id, kind=kind, payload=rel.model_dump(mode="json")),
        )
        return rel

    for row in index.execute("SELECT * FROM companies GROUP BY SYNTHETIC_U"):
        cid, group = row["SYNTHETIC_U"], row["GROUP_ID"]
        if group and cid not in quarantine:
            await relation(
                "group",
                [cid, valid_id(group), cid],
                sources["companies"],
                row_ref(sources["companies"], row, "companies"),
                "unverified",
            )
    for row in index.execute("SELECT * FROM links GROUP BY row_hash ORDER BY row_no"):
        cid, fid = valid_id(row["SYNTHETIC_U"]), valid_id(row["SYNTHETIC_F"])
        await relation(
            "shared_person",
            [cid, fid, cid],
            sources["links"],
            row_ref(sources["links"], row, "links"),
            "unverified",
        )
    source = sources["payments"]
    source_payload = source.ref.model_dump(mode="json")
    period = PAYMENT_PERIOD.model_dump(mode="json")
    for row in index.execute(PAYMENT_ROWS_SQL):
        payer, beneficiary = valid_id(row["PAYER"]), valid_id(row["BENEFICIARY"])
        kind = "self_transfer" if row["self_edge"] else "observed_payment"
        nodes = [payer, beneficiary]
        rid = stable_id("relation", source.id, kind, nodes, row["row_no"])
        ref = {
            **source_payload,
            "row": row["row_no"],
            "span": json.dumps({key: row[key] for key in SCHEMAS["payments"]}, ensure_ascii=False),
        }
        # All input validation/Decimal rules ran in audit. Preserve the same public
        # DTO fields/IDs as the reference Pydantic path; do not aggregate noisy pairs.
        relation_payload = {
            "schema_version": SCHEMA_VERSION,
            "id": rid,
            "kind": kind,
            "nodes": nodes,
            "provenance": [ref],
            "quality": "verified",
            "economic_role": "unknown",
        }
        await add(
            t.relations, dict(id=rid, source_id=source.id, kind=kind, payload=relation_payload)
        )
        for name, normalized, unit in (
            ("PAY_AMT", "normalized_amount", "RUB"),
            ("PAY_CNT", "normalized_count", "count"),
        ):
            fid = stable_id("feature", rid, name)
            feature_ref = {
                **ref,
                "field": name,
                "semantic_source_ref": PAYMENT_SEMANTIC_SOURCE,
            }
            payload = {
                "schema_version": SCHEMA_VERSION,
                "id": fid,
                "name": name,
                "raw_value": row[name],
                "value": row[normalized],
                "quality": "verified",
                "unit": unit,
                "period": period,
                "available_at": None,
                "provenance": [feature_ref],
                "depends_on_source_versions": [source.id],
                "reason_codes": [],
            }
            await add(t.features, dict(id=fid, snapshot_id=snapshots[payer], payload=payload))
    await flush()
    return {
        "status": "imported",
        "source_version_ids": sorted(expected),
        "inserted": dict(counts),
        "grants_created": 0,
        "available_at": None,
        "coverage": "authorized_subset",
    }


async def run(input_dir, output, apply=False, db_engine=None):
    started = time.monotonic()
    with audited_inputs(input_dir) as (index, report):
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"import_version": IMPORT_VERSION, "audit": report, "status": "audit_failed"}
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        require_importable(report)
        if apply:
            manifest["status"] = "import_failed"
            output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            if db_engine is None:
                from app.core.db import engine

                db_engine = engine
            try:
                async with db_engine.begin() as conn:
                    result = await import_snapshot(conn, index, report)
            finally:
                await db_engine.dispose()
        else:
            result = {
                "status": "dry_run",
                "source_version_ids": sorted(s.id for s in source_versions(report).values()),
                "database_written": False,
            }
        manifest.update(result)
        manifest["metrics"] = {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "audit_seconds": report["elapsed_seconds"],
        }
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/manifests/csv-import.json"))
    parser.add_argument(
        "--apply", action="store_true", help="Explicitly write one atomic transaction"
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.input_dir, args.output, args.apply))
    print(json.dumps({"status": result["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
