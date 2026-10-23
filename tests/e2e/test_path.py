import os
import time
from uuid import uuid4

import httpx
import pytest

from contracts.v1 import Analysis, CompanyProfile, Job, SessionResponse, UserContext

pytestmark = pytest.mark.e2e
BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8000")


def login(actor):
    c = httpx.Client(base_url=BASE, timeout=10)
    r = c.post("/api/v1/demo/sessions", json={"actor": actor})
    assert r.status_code == 200, r.text
    session = SessionResponse.model_validate(r.json())
    c.headers["X-CSRF-Token"] = session.csrf_token
    assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    return c, session.context


def submit(c, ctx, **kw):
    body = {
        "event_id": "rate_hold",
        "context_version": ctx.version,
        "as_of": "2026-10-21T00:00:00Z",
        **kw,
    }
    key = str(uuid4())
    r = c.post("/api/v1/analyses", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == 202, r.text
    job = Job.model_validate(r.json())
    return body, key, job


def result(c, job):
    for _ in range(100):
        r = c.get(job.poll_url)
        assert r.status_code == 200, r.text
        state = Job.model_validate(r.json())
        assert state.status != "failed", state
        if state.result_id:
            response = c.get("/api/v1/analyses/" + state.result_id)
            assert response.status_code == 200, response.text
            return Analysis.model_validate(response.json())
        time.sleep(0.1)
    pytest.fail("worker did not complete job")


def test_vertical_isolation_role_idempotency_followup():
    with httpx.Client(base_url=BASE) as anonymous:
        assert anonymous.get("/api/v1/companies").status_code == 401
        assert (
            anonymous.post(
                "/api/v1/demo/sessions",
                json={"actor": "alice"},
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
    a, ctx = login("alice")
    b, bctx = login("bob")
    try:
        assert a.get("/health/ready").status_code == 200
        assert "DEMO" in a.get("/").text
        companies = [CompanyProfile.model_validate(x) for x in a.get("/api/v1/companies").json()]
        assert {p.company_id for p in companies} == {"demo_a", "demo_b"}
        assert all("demo_hidden" not in p.group.visible_company_ids for p in companies)
        for path in [
            "companies/demo_hidden/profile",
            "evidence/demo_hidden_evidence",
            "sources/demo_hidden_doc",
        ]:
            assert a.get("/api/v1/" + path).status_code == 404
        assert (
            a.patch(
                "/api/v1/me/context",
                json={"expected_version": ctx.version, "company_id": "demo_hidden"},
            ).status_code
            == 404
        )
        no_csrf = httpx.Client(base_url=BASE, cookies=a.cookies)
        assert (
            no_csrf.patch(
                "/api/v1/me/context", json={"expected_version": ctx.version, "role": "ceo"}
            ).status_code
            == 403
        )
        no_csrf.close()
        body, key, job = submit(a, ctx)
        again = a.post("/api/v1/analyses", json=body, headers={"Idempotency-Key": key})
        assert again.json()["id"] == job.id
        conflict = a.post(
            "/api/v1/analyses",
            json={**body, "as_of": "2026-10-22T00:00:00Z"},
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        out = result(a, job)
        assert (
            out.domain == "synthetic" and out.model_id is None and out.usage_status == "not_called"
        )
        assert out.bank_evidence_status == "not_found" and out.sections.bank_view == []
        assert {"investment", "ai", "synthetic", "time"} <= {n.id for n in out.notices}
        assert out.delivery.mode == "saved_replay"
        assert "aggregate_end_unknown" in out.temporal.warnings
        assert b.get(job.poll_url).status_code == 404
        assert b.get("/api/v1/analyses/" + out.id).status_code == 404
        # Updating role does not grant access or mutate facts, event or bank claims.
        switched = a.patch(
            "/api/v1/me/context",
            json={
                "expected_version": ctx.version,
                "role": "cfo",
                "preferences": {"needs": ["finance"]},
            },
        )
        assert switched.status_code == 200, switched.text
        new = UserContext.model_validate(switched.json())
        assert new.grants == ctx.grants
        assert (
            a.patch(
                "/api/v1/me/context", json={"expected_version": ctx.version, "role": "ceo"}
            ).status_code
            == 409
        )
        assert b.get("/api/v1/me/context").json()["preferences"] == bctx.preferences.model_dump(
            mode="json"
        )
        _, _, job2 = submit(a, new)
        out2 = result(a, job2)
        assert out2.features == out.features and out2.evidence == out.evidence
        assert (
            out2.claims[0] == out.claims[0]
            and out2.bank_evidence_status == out.bank_evidence_status
        )
        follow = {
            "analysis_id": out.id,
            "context_version": ctx.version,
            "text": "Условие только Alice",
            "assumptions": [{"name": "rate", "value": "fixed through 2027"}],
        }
        r = a.post("/api/v1/followups", json=follow, headers={"Idempotency-Key": str(uuid4())})
        assert r.status_code == 202, r.text
        follow_result = result(a, Job.model_validate(r.json()))
        assert follow_result.assumptions[0].value == "fixed through 2027"
        assert follow_result.context.version == ctx.version
        assert "fixture_only" in follow_result.questions[0]
        assert (
            b.post(
                "/api/v1/followups", json=follow, headers={"Idempotency-Key": str(uuid4())}
            ).status_code
            == 404
        )
        _, _, bj = submit(b, bctx)
        assert result(b, bj).assumptions == []
    finally:
        a.close()
        b.close()


def test_strict_time_no_future_retrieval_or_snapshot_features():
    a, ctx = login("alice")
    try:
        _, _, j = submit(a, ctx, temporal_mode="strict_as_of")
        out = result(a, j)
        assert out.features == [] and out.temporal.temporal_status == "excluded"
        assert {"availability_unknown", "aggregate_end_unknown"} <= set(out.temporal.warnings)
        _, _, j = submit(a, ctx, as_of="2026-09-01T00:00:00Z")
        out = result(a, j)
        assert out.evidence == [] and out.claims == [] and out.status == "insufficient_evidence"
        # No grants/principal spoofing through request extras or headers.
        r = a.post(
            "/api/v1/analyses",
            json={
                "event_id": "rate_hold",
                "context_version": ctx.version,
                "as_of": "2026-10-21T00:00:00Z",
                "principal_id": "bob",
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert r.status_code == 422
        assert (
            a.get(
                "/api/v1/companies/demo_hidden/profile", headers={"X-Client-Scope": "demo_hidden"}
            ).status_code
            == 404
        )
    finally:
        a.close()
