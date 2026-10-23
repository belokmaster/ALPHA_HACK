"""Explicit local input audit; never imports raw CSV into application tables.

Run ``python -m app.ingestion.csv.audit --input-dir PATH --output PATH``.
SQLite is a temporary, disk-backed audit index, not the application's database.
"""

import argparse
import csv
import hashlib
import json
import sqlite3
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.ingestion.csv.normalization import (
    FLAG_FIELDS,
    PAYMENT_PERIOD,
    PAYMENT_SEMANTIC_SOURCE,
    RULE_VERSION,
    normalize_client_type,
    normalize_flag,
    normalize_payment,
    normalize_revenue,
)

FILES = {
    "companies": "df_Uid_data_hack.csv",
    "links": "df_Fid_Uid_link_hack.csv",
    "payments": "df_Uid_payment_hack.csv",
}
SCHEMAS = {
    "companies": (
        "SYNTHETIC_U",
        "ORG_TYPE",
        "OKFS_NAME",
        "COMPANY_AGE_YEARS_GRADATION",
        "ECO_MARK_LABEL",
        "MAINOKVED_CODE",
        "MAINOKVED_NAME",
        "NEGATIVEMARK",
        "AMOUNT_WORKERS_FNS_GRADATION",
        "AMOUNT_WORKERS_SPARK_GRADATION",
        "CL_TYPE",
        "CL_PRODUCT_RKO_MARK",
        "CL_PRODUCT_GAR_MARK",
        "CL_PRODUCT_LOA_MARK",
        "CL_PRODUCT_LEA_MARK",
        "CL_PRODUCT_DEP_MARK",
        "CL_PRODUCT_ZPP_MARK",
        "FTS_FLAG",
        "FTS_COUNTRY_NAME",
        "FACTORING_MARK",
        "REVENUE_COMPANY",
        "GROUP_ID",
        "CITY",
        "REGION",
    ),
    "links": ("SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"),
    "payments": ("BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"),
}


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _one(db: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> int:
    return int(db.execute(sql, args).fetchone()[0])


def _ratio(numerator: Decimal | int, denominator: Decimal | int) -> str | None:
    return str(Decimal(numerator) / Decimal(denominator)) if denominator else None


def _sums(rows: Iterable[tuple[str, str, int]]) -> dict[str, Any]:
    amount = Decimal(0)
    count = 0
    row_count = 0
    self_amount = Decimal(0)
    self_count = 0
    self_rows = 0
    for raw_amount, raw_count, is_self in rows:
        value, number = Decimal(raw_amount), int(raw_count)
        amount += value
        count += number
        row_count += 1
        if is_self:
            self_amount += value
            self_count += number
            self_rows += 1
    return {
        "rows": row_count,
        "amount_rub": str(amount),
        "payment_count": count,
        "self_transfer": {
            "rows": self_rows,
            "amount_rub": str(self_amount),
            "payment_count": self_count,
            "share_amount": _ratio(self_amount, amount),
            "share_payment_count": _ratio(self_count, count),
            "share_rows": _ratio(self_rows, row_count),
        },
        "intercompany": {
            "rows": row_count - self_rows,
            "amount_rub": str(amount - self_amount),
            "payment_count": count - self_count,
        },
    }


def _audit_file(db: sqlite3.Connection, kind: str, path: Path) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "filename": path.name,
        "sha256": _hash_file(path),
        "bytes": path.stat().st_size,
        "encoding": "utf-8-sig",
        "delimiter": ";",
        "parser_version": "csv-audit-1",
        "rule_version": RULE_VERSION,
    }
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = next(reader, [])
        result["schema"] = header
        result["expected_schema"] = list(SCHEMAS[kind])
        if len(header) != len(set(header)) or tuple(header) != SCHEMAS[kind]:
            result.update(status="schema-mismatch", rows=None)
            return result
        columns = ", ".join(f"{_identifier(col)} TEXT" for col in header)
        extras = ", normalized_amount TEXT, normalized_count TEXT, valid INTEGER, self_edge INTEGER"
        db.execute(
            f"CREATE TABLE {kind} (row_no INTEGER, row_hash TEXT, {columns}"
            f"{extras if kind == 'payments' else ''})"
        )
        placeholders = ",".join("?" for _ in range(len(header) + (6 if kind == "payments" else 2)))
        insert = f"INSERT INTO {kind} VALUES ({placeholders})"
        pending: list[tuple[Any, ...]] = []
        nulls: Counter[str] = Counter()
        issues: Counter[str] = Counter()
        client_types: Counter[str] = Counter()
        malformed: list[int] = []
        total_rows, accepted_rows = 0, 0
        for number, values in enumerate(reader, start=2):
            total_rows += 1
            if len(values) != len(header):
                issues["malformed_row"] += 1
                if len(malformed) < 20:
                    malformed.append(number)
                continue
            accepted_rows += 1
            row = dict(zip(header, values, strict=True))
            nulls.update(key for key, value in row.items() if not value.strip())
            signature = hashlib.sha256(
                json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            extra: tuple[Any, ...] = ()
            if kind == "payments":
                payment = normalize_payment(row)
                issues.update(f"PAY_AMT:{code}" for code in payment.amount.reason_codes)
                issues.update(f"PAY_CNT:{code}" for code in payment.count.reason_codes)
                issues.update(payment.reason_codes)
                extra = (
                    str(payment.amount.value) if payment.amount.value is not None else None,
                    str(payment.count.value) if payment.count.value is not None else None,
                    int(payment.valid),
                    int(payment.kind == "self_transfer"),
                )
            elif kind == "companies":
                issues.update(
                    f"REVENUE_COMPANY:{code}"
                    for code in normalize_revenue(row["REVENUE_COMPANY"]).reason_codes
                )
                for flag in FLAG_FIELDS:
                    issues.update(
                        f"{flag}:{code}" for code in normalize_flag(row[flag]).reason_codes
                    )
                client_type = normalize_client_type(row["CL_TYPE"])
                client_types[str(client_type.value)] += 1
                issues.update(f"CL_TYPE:{code}" for code in client_type.reason_codes)
                if not row["SYNTHETIC_U"].strip():
                    issues["missing_company_id"] += 1
            else:
                # Both raw values and conflicting definitions remain in the audit.
                issues["CITY_NAME:semantic_conflict"] += 1
                issues["RC_NAME:semantic_conflict"] += 1
            pending.append((number, signature, *values, *extra))
            if len(pending) >= 10_000:
                db.executemany(insert, pending)
                pending.clear()
        if pending:
            db.executemany(insert, pending)
    db.commit()
    db.execute(f"CREATE INDEX {kind}_hash ON {kind}(row_hash)")
    unique = {
        col: _one(
            db,
            f"SELECT COUNT(DISTINCT {_identifier(col)}) FROM {kind} "
            f"WHERE TRIM({_identifier(col)}) != ''",
        )
        for col in header
    }
    exact = accepted_rows - _one(db, f"SELECT COUNT(DISTINCT row_hash) FROM {kind}")
    result.update(
        status="measured",
        rows=total_rows,
        parsed_rows=accepted_rows,
        malformed_row_examples=malformed,
        nulls={col: nulls[col] for col in header},
        unique_non_null=unique,
        exact_duplicate_rows=exact,
        issues=dict(sorted(issues.items())),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    if client_types:
        result["client_types"] = dict(sorted(client_types.items()))
    if _hash_file(path) != result["sha256"]:
        result["status"] = "source-changed"
    return result


def _company_diagnostics(db: sqlite3.Connection, report: dict[str, Any]) -> None:
    result = report["files"]["companies"]
    db.execute("CREATE INDEX companies_id ON companies(SYNTHETIC_U)")
    repeated = list(
        db.execute(
            "SELECT SYNTHETIC_U, COUNT(*), COUNT(DISTINCT row_hash) FROM companies "
            "WHERE TRIM(SYNTHETIC_U) != '' GROUP BY SYNTHETIC_U HAVING COUNT(*) > 1"
        )
    )
    quarantine: list[dict[str, Any]] = []
    for company_id, row_count, versions in repeated:
        if versions <= 1:
            continue
        fields = []
        for field in SCHEMAS["companies"]:
            count = _one(
                db,
                f"SELECT COUNT(DISTINCT {_identifier(field)}) FROM companies WHERE SYNTHETIC_U = ?",
                (company_id,),
            )
            if count > 1:
                fields.append(field)
        row_numbers = [
            row[0]
            for row in db.execute(
                "SELECT row_no FROM companies WHERE SYNTHETIC_U = ? ORDER BY row_no",
                (company_id,),
            )
        ]
        quarantine.append(
            {
                "company_id": company_id,
                "rows": row_count,
                "distinct_versions": versions,
                "row_numbers": row_numbers,
                "conflicting_fields": fields,
                "code": "conflicting_company_id",
                "safe_behavior": "quarantine_all_versions",
            }
        )
    result["repeated_ids"] = len(repeated)
    result["quarantined_ids"] = len(quarantine)
    result["quarantined_rows"] = sum(item["rows"] for item in quarantine)
    result["quarantine"] = quarantine
    result["acl_inferred"] = False
    result["group_consolidation"] = "not_performed"
    result["negative_mark_semantics"] = "bank_has_reliable_key_owner_information_not_risk"
    result["fts_semantics"] = "countries_only_no_direction_currency_or_goods"


def _payment_diagnostics(db: sqlite3.Connection, report: dict[str, Any]) -> None:
    result = report["files"]["payments"]
    db.execute("CREATE INDEX payments_pair ON payments(PAYER, BENEFICIARY)")
    db.execute(
        "CREATE TABLE payment_pairs AS SELECT PAYER, BENEFICIARY, COUNT(*) AS n, "
        "COUNT(DISTINCT row_hash) AS versions, CAST(MIN(row_no) AS INTEGER) AS first_row "
        "FROM payments GROUP BY PAYER, BENEFICIARY"
    )
    # MIN(row_no) has no SQLite affinity; an implicit index on versions alone
    # degenerates into a quadratic join on full inputs. Index the join key explicitly.
    db.execute("CREATE INDEX payment_pairs_first_row ON payment_pairs(first_row)")
    db.execute("CREATE INDEX payments_row_no ON payments(row_no)")
    result["unique_pairs"] = _one(db, "SELECT COUNT(*) FROM payment_pairs")
    result["repeated_pairs"] = _one(db, "SELECT COUNT(*) FROM payment_pairs WHERE n > 1")
    result["heterogeneous_repeated_pairs"] = _one(
        db,
        "SELECT COUNT(*) FROM payment_pairs WHERE versions > 1",
    )
    result["rows_in_heterogeneous_pairs"] = _one(
        db,
        "SELECT COALESCE(SUM(n), 0) FROM payment_pairs WHERE versions > 1",
    )
    result["affected_payers_without_exact_share"] = _one(
        db,
        "SELECT COUNT(DISTINCT PAYER) FROM payment_pairs WHERE versions > 1",
    )
    result["invalid_rows"] = _one(db, "SELECT COUNT(*) FROM payments WHERE valid = 0")
    result["issue_codes"] = [
        code
        for condition, code in (
            (result["exact_duplicate_rows"], "exact_payment_duplicate"),
            (result["heterogeneous_repeated_pairs"], "heterogeneous_repeated_pair"),
        )
        if condition
    ]
    result["raw_valid_diagnostics"] = _sums(
        db.execute(
            "SELECT normalized_amount, normalized_count, self_edge FROM payments WHERE valid = 1",
        )
    )
    result["raw_valid_diagnostics"]["basis"] = "valid_input_rows_before_deduplication"
    result["normalized_diagnostics"] = _sums(
        db.execute(
            "SELECT p.normalized_amount, p.normalized_count, p.self_edge FROM payments p "
            "JOIN payment_pairs k ON p.row_no = k.first_row WHERE k.versions = 1 AND p.valid = 1",
        )
    )
    result["normalized_diagnostics"]["basis"] = (
        "exact_raw_duplicates_removed_heterogeneous_pairs_and_invalid_rows_excluded"
    )
    result["normalized_diagnostics"]["safe_behavior"] = (
        "diagnostics_only_not_complete_exposure_denominators_for_affected_payers"
    )
    result["unit"] = "RUB"
    result["count_unit"] = "count"
    result["period"] = {"start": PAYMENT_PERIOD.start.isoformat(), "end": None}
    result["period_source"] = PAYMENT_SEMANTIC_SOURCE
    result["available_at"] = None
    result["economic_role"] = "unknown"
    if report["files"].get("companies", {}).get("status") == "measured":
        db.execute(
            "CREATE TABLE payment_nodes AS SELECT PAYER AS id FROM payments WHERE TRIM(PAYER) != '' "
            "UNION SELECT BENEFICIARY AS id FROM payments WHERE TRIM(BENEFICIARY) != ''"
        )
        result["unique_endpoints"] = _one(db, "SELECT COUNT(*) FROM payment_nodes")
        result["stub_nodes"] = _one(
            db,
            "SELECT COUNT(*) FROM payment_nodes n WHERE NOT EXISTS "
            "(SELECT 1 FROM companies c WHERE c.SYNTHETIC_U = n.id)",
        )
    else:
        result["stub_nodes"] = None


def _link_diagnostics(db: sqlite3.Connection, report: dict[str, Any]) -> None:
    result = report["files"]["links"]
    result["semantic_conflicts"] = {
        "CITY_NAME": {
            "webinar": "legal_entity_service_city",
            "case_page_16": "individual_residence_city",
        },
        "RC_NAME": {
            "webinar": "legal_entity_service_region",
            "case_page_16": "individual_residence_region",
        },
    }
    result["geographic_impact_enabled"] = False
    result["app_principals_inferred"] = False
    if report["files"].get("companies", {}).get("status") == "measured":
        result["unknown_company_nodes"] = _one(
            db,
            "SELECT COUNT(DISTINCT l.SYNTHETIC_U) FROM links l "
            "WHERE TRIM(l.SYNTHETIC_U) != '' AND NOT EXISTS "
            "(SELECT 1 FROM companies c WHERE c.SYNTHETIC_U = l.SYNTHETIC_U)",
        )


@contextmanager
def audited_inputs(input_dir: Path):
    """Audit the three canonical inputs without following links or reading other files."""
    started = time.monotonic()
    root = input_dir.resolve()
    report: dict[str, Any] = {
        "report_version": "1.1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "measured",
        "method": "streaming_csv_temporary_sqlite_index",
        "input_directory": str(root),
        "files": {},
        "missing_inputs": [],
        "semantic_source": "Вопросы с вебинара _ Новостная лента.md:10-25;сводка.md:Транскрипция вебинара",
        "availability_inferred": False,
        "imported_to_application": False,
        "notices": [
            "Full source measurement; no external API calls.",
            "Raw diagnostics and deduplicated diagnostics have distinct denominators.",
            "Payment window end does not prove availability; strict_as_of excludes unknown availability.",
            "Identifiers are anonymized; no inferred identities, ownership, ACL or counterparty roles.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="alfa-input-audit-") as temporary:
        db = sqlite3.connect(str(Path(temporary) / "audit.sqlite"))
        try:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-32768")
            for kind, filename in FILES.items():
                path = root / filename
                if not path.is_file() or path.is_symlink():
                    report["files"][kind] = {"filename": filename, "status": "missing-inputs"}
                    report["missing_inputs"].append(filename)
                    continue
                report["files"][kind] = _audit_file(db, kind, path)
            for kind, handler in (
                ("companies", _company_diagnostics),
                ("links", _link_diagnostics),
                ("payments", _payment_diagnostics),
            ):
                if report["files"][kind]["status"] == "measured":
                    handler(db, report)
            if report["missing_inputs"]:
                report["status"] = "missing-inputs"
            for failure in ("schema-mismatch", "source-changed"):
                if any(f["status"] == failure for f in report["files"].values()):
                    report["status"] = failure
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            yield db, report
        finally:
            db.close()


def audit_inputs(input_dir: Path) -> dict[str, Any]:
    with audited_inputs(input_dir) as (_, report):
        return report


def require_importable(report: dict[str, Any]) -> None:
    """Import must fail closed; measured diagnostics alone do not mean valid input."""
    if report["status"] != "measured":
        raise ValueError("CSV audit failed: " + report["status"])
    for item in report["files"].values():
        if item.get("issues", {}).get("malformed_row", 0):
            raise ValueError("Malformed CSV rows; repair the source before import")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_inputs(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "elapsed_seconds": report["elapsed_seconds"],
            },
            ensure_ascii=False,
        )
    )
    if report["status"] != "measured":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
