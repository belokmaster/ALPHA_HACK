from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from contracts.v1 import AnalysisRequest, APIError, ContextPatch, ErrorEnvelope, GroupContext


def test_utc_extra_and_decimal():
    with pytest.raises(ValidationError):
        AnalysisRequest(event_id="a", context_version=1, as_of=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        ContextPatch(expected_version=1, grants=[])
    assert (
        GroupContext(observed_group_revenue_sum=Decimal("123.01")).model_dump(mode="json")[
            "observed_group_revenue_sum"
        ]
        == "123.01"
    )
    assert list(
        ErrorEnvelope(
            error=APIError(code="not_found", message="missing", request_id="test")
        ).model_dump()
    ) == ["error"]


def test_examples_and_missing_evidence_rejected():
    from contracts.examples import examples
    from contracts.v1 import Analysis

    for obj in examples():
        assert type(obj).model_validate_json(obj.model_dump_json()) == obj
        if isinstance(obj, Analysis):
            broken = obj.model_dump(mode="json")
            broken["claims"][0]["evidence_ids"] = ["nonexistent"]
            with pytest.raises(ValidationError, match="missing evidence"):
                Analysis.model_validate(broken)
            broken = obj.model_dump(mode="json")
            broken["claims"][0]["kind"] = "bank_view"
            broken["claims"][0]["origin"] = "bank_analysis"
            with pytest.raises(ValidationError, match="false bank attribution"):
                Analysis.model_validate(broken)


def test_official_qa_open_period_and_legacy_closed_period():
    from datetime import date

    from contracts.v1 import Period

    period = Period(start=date(2026, 1, 1))
    assert period.end is None and period.model_dump(mode="json")["end"] is None
    assert Period.model_validate(
        {"schema_version": "1.0.0", "start": "2025-01-01", "end": "2025-12-31"}
    ).end == date(2025, 12, 31)
    with pytest.raises(ValidationError, match="schema 1.1.0"):
        Period(schema_version="1.0.0", start=date(2026, 1, 1))


def test_official_qa_claim_attribution_and_hypotheses():
    from contracts.v1 import Claim

    with pytest.raises(ValidationError, match="origin must be explicit"):
        Claim(id="c", kind="fact", text="Unknown attribution", check="supported")
    with pytest.raises(ValidationError, match="kind and origin"):
        Claim(id="bad", kind="fact", origin="model", text="Model opinion", check="supported")
    legacy = Claim(schema_version="1.0.0", id="c", kind="fact", text="Legacy", check="supported")
    assert legacy.origin == "unknown"  # Reading history does not invent a publisher.
    with pytest.raises(ValidationError, match="conditions"):
        Claim(
            id="h", kind="hypothesis", origin="model", text="A possible effect", check="supported"
        )
    hypothesis = Claim(
        id="h",
        kind="hypothesis",
        origin="model",
        text="A possible effect",
        conditions=["Verify the contract"],
        check="conditional",
    )
    assert hypothesis.kind != "ai_inference" and hypothesis.kind != "fact"


def test_external_fact_needs_no_bank_thesis():
    from contracts.examples import examples
    from contracts.v1 import Analysis

    result = next(item for item in examples() if isinstance(item, Analysis)).model_dump(mode="json")
    result["domain"] = "live"
    result["status"] = "ready"
    result["claims"][0]["origin"] = "external_source"
    result["evidence"][0]["domain"] = "live"
    assert Analysis.model_validate(result).bank_evidence_status == "not_found"
