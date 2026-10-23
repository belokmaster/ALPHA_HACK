import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.csv.audit import FILES, SCHEMAS, audit_inputs
from app.ingestion.csv.normalization import (
    PAYMENT_PERIOD,
    REVENUE_PERIOD,
    assess_time,
    classify_payment_duplicates,
    counterparty_node,
    eligible_client,
    normalize_client_type,
    normalize_flag,
    normalize_fts_countries,
    normalize_owner_information,
    normalize_payment,
    normalize_payment_count,
    normalize_revenue,
    normalize_service_geography,
    quarantine_companies,
)
from contracts.v1 import Feature, Quality, SourceRef, TemporalMode


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("1.0", True), ("0", False), ("0.0", False)]
)
def test_flags_known(raw: str, expected: bool) -> None:
    result = normalize_flag(raw)
    assert result.value is expected
    assert result.raw_value == raw
    assert result.quality == Quality.verified


@pytest.mark.parametrize("raw", [None, "", "true", "-1", "2", "nan", "1.00"])
def test_flags_unknown_not_false(raw: str | None) -> None:
    result = normalize_flag(raw)
    assert result.value is None
    assert result.quality == Quality.unknown
    assert result.reason_codes


@pytest.mark.parametrize("raw", ["1000", "1000.0", "1000.000", "1E3"])
def test_revenue_placeholder_raw_preserved(raw: str) -> None:
    result = normalize_revenue(raw)
    assert result.raw_value == raw
    assert result.value is None
    assert result.reason_codes == ("technical_placeholder",)
    assert result.unit == "RUB"
    assert result.period == REVENUE_PERIOD
    assert result.semantic_source_ref and "W01" in result.semantic_source_ref


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", Decimal(0)), ("1001", Decimal(1001)), ("17.01", Decimal("17.01"))]
)
def test_revenue_exact_valid_amount(raw: str, expected: Decimal) -> None:
    normalized = normalize_revenue(raw)
    assert normalized.value == expected
    assert isinstance(normalized.value, Decimal)
    assert normalized.quality == Quality.verified


@pytest.mark.parametrize("raw", [None, "", "-1", "NaN", "Infinity", "garbage"])
def test_revenue_bad_values_not_zero(raw: str | None) -> None:
    result = normalize_revenue(raw)
    assert result.value is None
    assert result.quality == Quality.unknown


def payment(
    amount: str = "17.01", count: str = "2", payer: str = "U1", beneficiary: str = "U2"
) -> dict[str, str]:
    return {"PAYER": payer, "BENEFICIARY": beneficiary, "PAY_AMT": amount, "PAY_CNT": count}


def test_payment_units_precision_window_and_unknown_role() -> None:
    result = normalize_payment(payment())
    assert result.amount.value == Decimal("17.01")
    assert result.count.value == 2
    assert isinstance(result.count.value, int)
    assert result.amount.unit == "RUB"
    assert result.count.unit == "count"
    assert result.amount.period == PAYMENT_PERIOD
    assert result.amount.period.start == date(2026, 1, 1)
    assert result.amount.period.end is None
    assert result.economic_role == "unknown"
    assert result.kind == "observed_payment"
    assert result.intercompany_eligible


@pytest.mark.parametrize("raw", ["1.1", "-1", "NaN", "", "Infinity"])
def test_bad_count_stays_unknown(raw: str) -> None:
    assert normalize_payment_count(raw).value is None


def test_payment_zero_is_preserved_and_consistency_checked() -> None:
    zero = normalize_payment(payment("0", "0"))
    assert zero.valid
    assert zero.amount.value == 0
    assert zero.count.value == 0
    for amount, count in [("0", "1"), ("1", "0")]:
        inconsistent = normalize_payment(payment(amount, count))
        assert not inconsistent.valid
        assert inconsistent.reason_codes == ("inconsistent_zero_aggregate",)
        assert inconsistent.amount.raw_value == amount
        assert inconsistent.count.raw_value == count


def test_self_transfer_is_separate_not_group_or_exposure() -> None:
    result = normalize_payment(payment(payer="U1", beneficiary="U1"))
    assert result.valid
    assert result.kind == "self_transfer"
    assert not result.intercompany_eligible


def test_missing_endpoints_do_not_form_fake_self_edge() -> None:
    result = normalize_payment(payment(payer="", beneficiary=""))
    assert not result.valid
    assert result.kind == "observed_payment"
    assert result.reason_codes == ("missing_endpoint",)


def test_exact_duplicates_and_heterogeneous_pairs_never_added() -> None:
    first = payment("2", "1")
    second = payment("3", "1")
    unrelated = payment("7", "1", "U3", "U4")
    result = classify_payment_duplicates([first, first, second, unrelated])
    assert result.exact_duplicate_rows == (3,)
    assert result.repeated_pairs == (("U1", "U2"),)
    assert result.quarantined_rows == (2, 4)
    assert result.affected_payers == ("U1",)
    assert result.issue_codes == ("exact_payment_duplicate", "heterogeneous_repeated_pair")
    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].amount.value == Decimal(7)


def test_invalid_variant_quarantines_entire_repeated_pair() -> None:
    result = classify_payment_duplicates([payment(), payment("invalid", "2")])
    assert result.repeated_pairs == (("U1", "U2"),)
    assert not result.normalized_rows
    assert result.quarantined_rows == (2, 3)


def test_exact_duplicate_retains_one_raw_payment_only() -> None:
    result = classify_payment_duplicates([payment(), payment()])
    assert len(result.normalized_rows) == 1
    assert not result.repeated_pairs
    assert result.exact_duplicate_rows == (3,)


def test_conflicting_company_id_quarantines_all_versions() -> None:
    rows = [
        {"SYNTHETIC_U": "U1", "CL_TYPE": "CLIENT"},
        {"SYNTHETIC_U": "U1", "CL_TYPE": "PROSPECT"},
        {"SYNTHETIC_U": "U2", "CL_TYPE": "CLIENT"},
    ]
    result = quarantine_companies(rows)
    assert result["U1"].status == "quarantined"
    assert result["U1"].conflicting_fields == ("CL_TYPE",)
    assert len(result["U1"].rows) == 2
    assert result["U2"].status == "valid"


def test_identical_company_rows_are_not_conflicting() -> None:
    row = {"SYNTHETIC_U": "U1", "CL_TYPE": "CLIENT"}
    result = quarantine_companies([row, row])["U1"]
    assert result.status == "valid"
    assert result.issue_codes == ("exact_company_duplicate",)


@pytest.mark.parametrize("client_type", ["CLIENT", "PROSPECT", "CLOSE_CLIENT"])
def test_client_type_never_grants_access(client_type: str) -> None:
    assert normalize_client_type(client_type).value == client_type
    assert not eligible_client(client_type, has_server_grant=False)
    assert eligible_client(client_type, has_server_grant=True) == (client_type == "CLIENT")


def test_unrecognized_client_type_is_unknown() -> None:
    assert normalize_client_type("PRIVATE").value == "unknown"
    assert not eligible_client("PRIVATE", True)


@pytest.mark.parametrize("field", ["CITY_NAME", "RC_NAME"])
def test_service_geography_preserves_conflict_without_residence_inference(field: str) -> None:
    result = normalize_service_geography(field, "Москва")
    assert result.raw_value == "Москва"
    assert result.quality == Quality.conflict
    assert result.reason_codes == ("semantic_conflict",)
    assert result.interpretations["webinar"].startswith("legal_entity_service_")
    assert result.interpretations["case_page_16"].startswith("individual_residence_")
    assert not result.usable_for_geographic_impact


def test_company_geography_not_relabelled() -> None:
    with pytest.raises(ValueError):
        normalize_service_geography("CITY", "Москва")


def test_negative_mark_does_not_create_negative_owner_risk() -> None:
    value = normalize_owner_information("1")
    assert value.value is True
    assert value.interpretations["webinar"] == "bank_has_reliable_key_owner_information"


def test_fts_does_not_infer_import_direction_or_currency() -> None:
    value = normalize_fts_countries("КИТАЙ")
    assert value.value == "КИТАЙ"
    assert value.reason_codes == ("countries_only",)
    assert value.quality == Quality.unverified


def test_unknown_counterparty_is_stub_without_name_or_role() -> None:
    node = counterparty_node("U99", {"U1"})
    assert node.status == "stub"
    assert node.name is None
    assert node.economic_role == "unknown"


def test_amount_feature_uses_public_contract_exact_string_and_provenance() -> None:
    source = SourceRef(
        source_version_id="case-csv-v1",
        original_filename="source.csv",
        sha256="a" * 64,
        row=5,
        parser_version="csv-audit-1",
    )
    feature = normalize_revenue("17.01").to_feature("feature-revenue", "REVENUE_COMPANY", source)
    assert feature.value == "17.01"
    assert feature.raw_value == "17.01"
    assert feature.available_at is None
    assert feature.provenance[0].row == 5
    assert feature.provenance[0].semantic_source_ref.endswith("W01")
    assert feature.depends_on_source_versions == ["case-csv-v1"]
    assert Feature.model_validate_json(feature.model_dump_json()) == feature


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def test_strict_temporal_excludes_future_aggregate_and_unknown_availability() -> None:
    assessment = assess_time("strict_as_of", NOW, None, PAYMENT_PERIOD, ["payments-v1"])
    assert assessment.temporal_status == "excluded"
    assert assessment.warnings == ["availability_unknown", "aggregate_end_unknown"]
    assert assessment.depends_on_source_versions == ["payments-v1"]


def test_period_end_does_not_prove_snapshot_availability() -> None:
    after = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assessment = assess_time("strict_as_of", after, None, PAYMENT_PERIOD, ["payments-v1"])
    assert assessment.temporal_status == "excluded"
    assert assessment.warnings == ["availability_unknown", "aggregate_end_unknown"]


def test_known_past_availability_and_completed_window_included() -> None:
    assessment = assess_time(
        TemporalMode.strict_as_of,
        NOW,
        NOW - timedelta(days=1),
        REVENUE_PERIOD,
        ["revenue-v1", "revenue-v1"],
    )
    assert assessment.temporal_status == "included"
    assert not assessment.warnings
    assert assessment.depends_on_source_versions == ["revenue-v1"]


def test_future_availability_excluded_even_past_period() -> None:
    assessment = assess_time("strict_as_of", NOW, NOW + timedelta(seconds=1), REVENUE_PERIOD, [])
    assert assessment.temporal_status == "excluded"
    assert assessment.warnings == ["future_availability"]


def test_snapshot_explicitly_is_not_historical_backtest() -> None:
    assessment = assess_time("snapshot_demo", NOW, None, PAYMENT_PERIOD, ["payments-v1"])
    assert assessment.temporal_status == "snapshot"
    assert "snapshot_not_historical_backtest" in assessment.warnings
    assert "aggregate_end_unknown" in assessment.warnings


def test_payment_unknown_end_excluded_even_with_known_availability():
    assessment = assess_time("strict_as_of", NOW, NOW, PAYMENT_PERIOD, ["payments-v2"])
    assert assessment.temporal_status == "excluded"
    assert assessment.warnings == ["aggregate_end_unknown"]


def test_known_future_end_is_still_excluded():
    from contracts.v1 import Period

    future = Period(start=date(2026, 1, 1), end=date(2026, 12, 31))
    assessment = assess_time("strict_as_of", NOW, NOW, future, ["known-future-window"])
    assert assessment.warnings == ["future_aggregate"]


@pytest.mark.parametrize("naive_available", [False, True])
def test_temporal_rejects_naive_datetimes(naive_available: bool) -> None:
    with pytest.raises(ValueError, match="timezone required"):
        assess_time(
            "strict_as_of",
            NOW if naive_available else NOW.replace(tzinfo=None),
            NOW.replace(tzinfo=None) if naive_available else NOW,
            None,
            [],
        )


def write_csv(root: Path, kind: str, rows: list[dict[str, str]]) -> None:
    with (root / FILES[kind]).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SCHEMAS[kind], delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def test_audit_missing_inputs_does_not_replay_old_measurements(tmp_path: Path) -> None:
    report = audit_inputs(tmp_path)
    assert report["status"] == "missing-inputs"
    assert len(report["missing_inputs"]) == 3
    assert all("rows" not in result for result in report["files"].values())


def test_audit_schema_mismatch_is_explicit(tmp_path: Path) -> None:
    (tmp_path / FILES["companies"]).write_text("wrong;columns\n1;2\n")
    result = audit_inputs(tmp_path)
    assert result["status"] == "schema-mismatch"
    assert result["files"]["companies"]["rows"] is None


def test_audit_streaming_counts_deduplication_quarantine_stubs_and_sums(tmp_path: Path) -> None:
    write_csv(
        tmp_path,
        "companies",
        [
            {"SYNTHETIC_U": "U1", "CL_TYPE": "CLIENT", "REVENUE_COMPANY": "1000"},
            {"SYNTHETIC_U": "U2", "CL_TYPE": "CLIENT"},
            {"SYNTHETIC_U": "U2", "CL_TYPE": "PROSPECT"},
        ],
    )
    write_csv(
        tmp_path,
        "links",
        [{"SYNTHETIC_F": "F1", "SYNTHETIC_U": "U1", "CITY_NAME": "Москва", "RC_NAME": "Регион"}],
    )
    write_csv(
        tmp_path,
        "payments",
        [
            payment("100", "1", beneficiary="U1"),
            payment("2", "2"),
            payment("2", "2"),
            payment("3", "2"),
            payment("7", "1", beneficiary="U99"),
        ],
    )
    report = audit_inputs(tmp_path)
    assert report["status"] == "measured"
    assert not report["imported_to_application"]
    companies = report["files"]["companies"]
    assert companies["quarantined_ids"] == 1
    assert companies["quarantined_rows"] == 2
    assert companies["quarantine"][0]["conflicting_fields"] == ["CL_TYPE"]
    assert companies["issues"]["REVENUE_COMPANY:technical_placeholder"] == 1
    payments = report["files"]["payments"]
    assert payments["rows"] == 5
    assert payments["exact_duplicate_rows"] == 1
    assert payments["heterogeneous_repeated_pairs"] == 1
    assert payments["rows_in_heterogeneous_pairs"] == 3
    assert payments["stub_nodes"] == 1
    assert Decimal(payments["raw_valid_diagnostics"]["amount_rub"]) == 114
    assert payments["raw_valid_diagnostics"]["payment_count"] == 8
    assert Decimal(payments["normalized_diagnostics"]["amount_rub"]) == 107
    assert payments["normalized_diagnostics"]["self_transfer"]["rows"] == 1
    assert not report["files"]["links"]["geographic_impact_enabled"]
    assert len(companies["sha256"]) == 64
