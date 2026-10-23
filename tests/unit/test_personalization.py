from uuid import uuid4

import pytest

from app.personalization import match_event, rank_events
from contracts.examples import examples
from contracts.v1 import AnalysisRequest, CompanyProfile, Event, Feature, UserContext


def inputs(loan=True, lease=True):
    ctx = next(v for v in examples() if isinstance(v, UserContext))
    p = CompanyProfile(
        company_id=ctx.company_id,
        snapshot_id="snapshot",
        source_version_id="src",
        name="Company",
        industry="52.29",
        client_type="CLIENT",
        status="valid",
        features=[],
    )
    p.features = [
        Feature(
            id=name,
            name=name,
            raw_value=str(value),
            value=value,
            quality="unverified" if name == "MAINOKVED_CODE" else "verified",
            provenance=[],
            depends_on_source_versions=[],
        )
        for name, value in [
            ("CL_PRODUCT_LOA_MARK", loan),
            ("CL_PRODUCT_LEA_MARK", lease),
            ("MAINOKVED_CODE", "52.29"),
        ]
    ]
    req = AnalysisRequest(event_id="any", context_version=ctx.version, as_of="2026-10-22T12:00:00Z")
    es = [
        Event(
            id=str(uuid4()),
            source_version_id="src",
            domain="case",
            title=title,
            event_at=date,
            available_at=None,
            evidence_ids=[],
            topics=topics,
        )
        for title, date, topics in [
            ("Ставка и решение", "2026-09-11T00:00:00Z", ["rates"]),
            ("Прогноз ставки", "2026-09-02T00:00:00Z", ["rates"]),
            ("Цены на дизельное топливо", "2026-10-21T00:00:00Z", ["inflation"]),
        ]
    ]
    return ctx, p, req, es


def test_two_companies_differ_from_observed_flags_not_ids():
    ctx, p, req, events = inputs()
    first = rank_events(p, events, ctx, req)
    p2 = p.model_copy(deep=True)
    p2.features[0].value = False
    second = rank_events(p2, events, ctx, req)
    assert [x.relevance.score for x in first] == [90, 90, 60]
    assert [x.relevance.score for x in second] == [60, 10, 10]
    assert first[0].event.topics == ["rates"] and second[0].event.topics == ["inflation"]
    p2.features[0].value = True
    p2.company_id = "never-seen-company"
    same = rank_events(p2, events, ctx, req)
    assert [x.relevance for x in first] == [x.relevance for x in same]
    for item in first + second:
        assert set(item.feature_ids) <= {f.id for f in p.features}
        assert item.relevance.score == sum(r.weight for r in item.relevance.reasons)
        assert all(
            r.caveat and set(r.feature_ids) <= set(item.feature_ids) for r in item.relevance.reasons
        )


def test_new_event_identity_has_no_effect_and_unsupported_is_not_forced_into_feed():
    ctx, p, req, events = inputs()
    original = match_event(p, events[0], req)
    unseen = events[0].model_copy(update={"id": "unseen-arbitrary-event"})
    assert match_event(p, unseen, req) == original
    irrelevant = unseen.model_copy(
        update={"topics": ["bank_license"], "title": "Отозвана лицензия банка"}
    )
    assert not rank_events(p, [irrelevant], ctx, req)


@pytest.mark.parametrize("mode", ["strict_as_of", "snapshot_demo"])
def test_time_filters_future_events_and_no_unknown_as_historical_fact(mode):
    ctx, p, req, events = inputs()
    req.temporal_mode = type(req.temporal_mode)(mode)
    future = events[0].model_copy(update={"event_at": req.as_of.replace(year=2027)})
    assert not rank_events(p, [future], ctx, req)
    if mode == "strict_as_of":
        assert not rank_events(p, events, ctx, req)


def test_lease_alone_does_not_prove_rate_sensitive_payments():
    _, p, req, events = inputs(loan=False)
    match = match_event(p, events[0], req)
    assert match.score == 10 and not match.eligible and "financing_exposure" in match.missing
