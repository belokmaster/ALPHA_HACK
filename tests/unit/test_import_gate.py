import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ingestion.csv.audit import FILES, audit_inputs, require_importable
from app.ingestion.csv.importer import run


async def test_dry_run_never_connects_to_database(case_csv, tmp_path):
    output = tmp_path / "manifest.json"
    result = await run(case_csv, output, db_engine=object())
    assert result["status"] == "dry_run" and not result["database_written"]
    assert json.loads(output.read_text())["audit"]["files"]["companies"]["rows"] == 5


async def test_incomplete_input_fails_before_ml_or_database(tmp_path):
    with pytest.raises(ValueError, match="missing-inputs"):
        await run(tmp_path, tmp_path / "manifest.json", apply=True, db_engine=object())
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/import.py"),
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode != 0 and b"missing-inputs" in result.stderr
    assert b"ModuleNotFoundError" not in result.stderr


def test_malformed_input_is_not_importable(case_csv):
    with (case_csv / FILES["payments"]).open("a") as stream:
        stream.write("wrong;row\n")
    with pytest.raises(ValueError, match="Malformed"):
        require_importable(audit_inputs(case_csv))


def test_missing_input_cli_returns_failure(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.ingestion.csv.audit",
            "--input-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "audit.json"),
        ],
        capture_output=True,
    )
    assert result.returncode == 2


def test_source_change_is_not_a_measured_audit(case_csv, monkeypatch):
    from app.ingestion.csv import audit

    original = audit._hash_file
    calls = {}

    def changing_hash(path):
        calls[path] = calls.get(path, 0) + 1
        return original(path) if calls[path] == 1 else "0" * 64

    monkeypatch.setattr(audit, "_hash_file", changing_hash)
    report = audit.audit_inputs(case_csv)
    assert report["status"] == "source-changed"
    with pytest.raises(ValueError, match="source-changed"):
        require_importable(report)


def test_payment_cursor_has_linear_work_budget(case_csv):
    from app.ingestion.csv.audit import FILES, audited_inputs
    from app.ingestion.csv.importer import PAYMENT_ROWS_SQL

    with (case_csv / FILES["payments"]).open("a") as stream:
        for number in range(20_000):
            stream.write(f"Ulinear{number};U1;1.25;1\n")
    with audited_inputs(case_csv) as (index, report):
        assert (
            dict((r[1], r[2]) for r in index.execute("PRAGMA table_info(payment_pairs)"))[
                "first_row"
            ]
            == "INT"
        )
        steps = 0

        def budget():
            nonlocal steps
            steps += 1000
            return int(steps > 10_000_000)

        index.set_progress_handler(budget, 1000)
        # The old ORDER BY p.row_no plan scanned all versions=1 pairs per row and
        # exhausts this VM instruction budget. No wall-clock timing assertion.
        assert sum(1 for _ in index.execute(PAYMENT_ROWS_SQL)) == 20_003
