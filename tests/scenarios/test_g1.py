"""Explicit real-input test stack only; no CI dependency on private CSV/PDF/weights.

Controlled transport is a fixture. Data/DB/API/worker/retrieval/source assets are real.
"""

import asyncio
import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from isolation import isolated_scenario
from sqlalchemy import delete, select, update

from app.core import tables as t
from contracts.v1 import Analysis, Job

BASE = os.environ.get("TEST_BASE_URL", "http://app:8000")
COMPANY = os.environ.get("INTEGRATION_COMPANY", "U_15728")
EVENT = "case-rate-20260911"


@pytest.fixture(autouse=True)
async def restore_committed_scenario_state(db):
    async with isolated_scenario(db):
        yield


async def login():
    client = httpx.AsyncClient(base_url=BASE, timeout=15)
    response = await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
    assert response.status_code == 200, response.text
    session = response.json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    response = await client.patch(
        "/api/v1/me/context",
        json={
            "expected_version": session["context"]["version"],
            "company_id": COMPANY,
            "role": "cfo",
        },
    )
    assert response.status_code == 200, response.text
    return client, response.json()


async def start(client, ctx, **overrides):
    response = await client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "event_id": EVENT,
            "context_version": ctx["version"],
            "as_of": "2026-09-14T00:00:00Z",
            **overrides,
        },
    )
    assert response.status_code == 202, response.text
    return Job.model_validate(response.json())


async def finish(client, job):
    for _ in range(300):
        response = await client.get(job.poll_url)
        assert response.status_code == 200, response.text
        state = Job.model_validate(response.json())
        assert state.status != "failed", state
        if state.result_id:
            response = await client.get("/api/v1/analyses/" + state.result_id)
            assert response.status_code == 200, response.text
            return Analysis.model_validate(response.json())
        await asyncio.sleep(0.2)
    pytest.fail("Separate worker did not finish the job")


async def test_g1_real_profile_stored_sources_followup_replay_context_and_acl(db):
    client, ctx = await login()
    try:
        result = await finish(client, await start(client, ctx))
        assert result.domain == "case" and result.event_id == EVENT
        assert (
            result.delivery.mode == "saved_replay" and result.delivery.original_mode == "controlled"
        )
        assert (
            result.model_id == "deepseek/deepseek-v4.1-flash" and result.usage_status == "provider"
        )
        assert result.bank_evidence_status == "background_only" and not result.sections.bank_view
        assert {
            "contract_rate_type",
            "repricing_date",
            "loan_balance",
            "independent_page_review",
        } <= set(result.missing_fields)
        assert any(c.origin == "model" and c.check == "conditional" for c in result.claims)
        assert all(e.quality != "verified" for e in result.evidence)
        loan = next(f for f in result.features if f.name == "CL_PRODUCT_LOA_MARK")
        assert loan.value is True and loan.provenance[0].row == 3972
        assert (
            loan.provenance[0].sha256
            == "cb48983806ce7ca0ae045a40c9627ccbe47cc72bfb54058db04df2386da4a983"
        )
        assert (await client.get("/api/v1/features/" + loan.id)).status_code == 200
        for ev in result.evidence:
            assert (await client.get("/api/v1/evidence/" + ev.id)).status_code == 200
            response = await client.get("/evidence/" + ev.id)
            assert response.status_code == 200 and ev.span in response.text
            assert (
                await client.get("/api/v1/sources/" + ev.document_version_id)
            ).status_code == 200
        source = result.evidence[0].document_version_id
        asset = await client.get("/api/v1/sources/" + source + "/asset")
        assert asset.status_code == 200 and asset.content.startswith(b"%PDF")
        assert asset.headers["content-type"] == "application/pdf"
        response = await client.post(
            "/api/v1/followups",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "analysis_id": result.id,
                "context_version": ctx["version"],
                "text": "ставка фиксирована до конца 2027",
                "assumptions": [
                    {"name": "contract_rate", "value": "ставка фиксирована до конца 2027"}
                ],
            },
        )
        assert response.status_code == 202, response.text
        follow = await finish(client, Job.model_validate(response.json()))
        assert (
            follow.parent_analysis_id == result.id
            and follow.assumptions[0].source == "user_declared"
        )
        assert any(c.origin == "user_declared" for c in follow.claims)
        assert any("фиксации" in c.text for c in follow.claims if c.origin == "model")
        assert [c.text for c in follow.claims if c.kind == "fact"] == [
            c.text for c in result.claims if c.kind == "fact"
        ]
        assert follow.context.version == ctx["version"]
        async with db.connect() as c:
            run = (
                await c.execute(select(t.runs.c.payload).where(t.runs.c.id == result.run_id))
            ).scalar_one()
            assert run["runner"] == "analysis_engine" and run["model_calls_used"] == 3
            assert "adaptive_plan" in run["checkpoints"]
            queued = await c.scalar(select(t.jobs.c.payload).where(t.jobs.c.id == result.job_id))
            snapshot = queued["llm_runtime"]
            durable = await c.scalar(
                select(t.metadata.tables["llm_runs"].c.runtime_snapshot).where(
                    t.metadata.tables["llm_runs"].c.id == result.run_id
                )
            )
            assert durable == snapshot
            assert any(snapshot["sha256"] in x for x in run["checkpoints"])

            calls = (
                (
                    await c.execute(
                        select(t.metadata.tables["model_calls"].c.id).where(
                            t.metadata.tables["model_calls"].c.job_id == result.job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert set(calls) == {
                result.job_id + "_" + stage for stage in ("plan", "draft", "verify")
            }
            assert run["tool_calls_used"] <= 6 and run["steps_used"] <= 12
        response = await client.patch(
            "/api/v1/me/context", json={"expected_version": ctx["version"], "role": "ceo"}
        )
        assert response.status_code == 200
        stale = await client.post(
            "/api/v1/followups",
            headers={"Idempotency-Key": str(uuid4())},
            json={"analysis_id": result.id, "context_version": ctx["version"], "text": "Продолжи"},
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "context_stale"
        # Saved result is explicitly pinned, rather than silently rewritten for CEO.
        reopened = Analysis.model_validate(
            (await client.get("/api/v1/analyses/" + result.id)).json()
        )
        assert reopened.context.role == "cfo" and reopened.claims == result.claims
        async with db.begin() as c:
            grant = (
                await c.execute(
                    select(t.grants.c.payload).where(
                        t.grants.c.principal_id == "alice", t.grants.c.company_id == COMPANY
                    )
                )
            ).scalar_one()
            await c.execute(
                delete(t.grants).where(
                    t.grants.c.principal_id == "alice", t.grants.c.company_id == COMPANY
                )
            )
            await c.execute(
                update(t.principals)
                .where(t.principals.c.id == "alice")
                .values(acl_version=t.principals.c.acl_version + 1)
            )
        try:
            for path in [
                result.id and "analyses/" + result.id,
                "jobs/" + result.job_id,
                "evidence/" + result.evidence[0].id,
                "sources/" + source,
                "sources/" + source + "/asset",
                "features/" + loan.id,
            ]:
                assert (await client.get("/api/v1/" + path)).status_code == 404
        finally:
            from sqlalchemy import insert

            async with db.begin() as c:
                await c.execute(
                    insert(t.grants).values(principal_id="alice", company_id=COMPANY, payload=grant)
                )
    finally:
        await client.aclose()


async def test_strict_unknown_availability_and_missing_profile_stops(db):
    client, ctx = await login()
    try:
        strict = await finish(client, await start(client, ctx, temporal_mode="strict_as_of"))
        assert (
            strict.status == "insufficient_evidence" and not strict.claims and not strict.evidence
        )
        assert strict.bank_evidence_status == "not_found" and strict.usage_status == "not_called"
        async with db.begin() as c:
            original = (
                await c.execute(
                    select(t.companies.c.payload).where(t.companies.c.company_id == COMPANY)
                )
            ).scalar_one()
            await c.execute(
                update(t.companies)
                .where(t.companies.c.company_id == COMPANY)
                .values(payload={**original, "features": []})
            )
        try:
            insufficient = await finish(client, await start(client, ctx))
            assert (
                insufficient.status == "insufficient_evidence"
                and insufficient.usage_status == "not_called"
            )
            assert "financing_exposure" in insufficient.missing_fields
            assert not any(c.origin == "model" for c in insufficient.claims)
        finally:
            async with db.begin() as c:
                await c.execute(
                    update(t.companies)
                    .where(t.companies.c.company_id == COMPANY)
                    .values(payload=original)
                )
    finally:
        await client.aclose()


async def test_no_bank_proof_can_keep_conditional_external_hypothesis(db):
    """Availability/ACL fixture only; do not present these as real historical dates."""
    from sqlalchemy.dialects.postgresql import insert

    from app.ingestion.csv.normalization import normalize_flag
    from contracts.v1 import (
        CompanyProfile,
        Document,
        Event,
        Evidence,
        Grant,
        SourceRef,
        SourceVersion,
    )

    stamp = "2026-09-01T00:00:00Z"
    sid = "controlled_scenario_source_" + uuid4().hex[:8]
    ref = SourceRef(
        source_version_id=sid,
        original_filename="controlled-fixture-not-real",
        sha256=hashlib.sha256(sid.encode()).hexdigest(),
        row=1,
    )
    source = SourceVersion(
        id=sid, ref=ref, domain="case", available_at=stamp, import_version="controlled-scenario"
    )
    feature = normalize_flag("1.0").to_feature(sid + "_loan", "CL_PRODUCT_LOA_MARK", ref)
    feature.available_at = stamp
    profile = CompanyProfile(
        company_id=sid + "_company",
        snapshot_id=sid + "_snapshot",
        source_version_id=sid,
        name="Controlled fixture, not real client data",
        industry=None,
        client_type="CLIENT",
        status="valid",
        features=[feature],
    )
    doc = Document(
        id=sid + "_doc",
        source_version_id=sid,
        domain="case",
        title="Controlled fixture",
        publisher="Controlled fixture, not a bank",
        published_at=stamp,
        available_at=stamp,
        text="Учебное решение: ставка оставлена без изменения.",
    )
    ev = Evidence(
        id=sid + "_ev",
        document_version_id=doc.id,
        source_version_id=sid,
        quote=doc.text,
        span=f"chars:0:{len(doc.text)}",
        value_kind="fact",
        quality="unverified",
        domain="case",
        available_at=stamp,
    )
    event = Event(
        id=sid + "_event",
        source_version_id=sid,
        domain="case",
        title="Ключевая ставка",
        event_at=stamp,
        available_at=stamp,
        evidence_ids=[ev.id],
        topics=["rates"],
    )
    grant = Grant(
        company_id=profile.company_id,
        scopes=["profile.read", "analysis.run", "news.read", "sources.read"],
    )
    async with db.begin() as c:
        await c.execute(
            insert(t.source_versions).values(
                id=sid,
                sha256=ref.sha256,
                import_version=source.import_version,
                payload=source.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.companies).values(
                id=profile.snapshot_id,
                company_id=profile.company_id,
                source_id=sid,
                payload=profile.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.features).values(
                id=feature.id,
                snapshot_id=profile.snapshot_id,
                payload=feature.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.documents).values(
                id=doc.id,
                company_id=profile.company_id,
                source_id=sid,
                payload=doc.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.evidence).values(
                id=ev.id, document_id=doc.id, payload=ev.model_dump(mode="json")
            )
        )
        await c.execute(
            insert(t.events).values(
                id=event.id, source_id=sid, payload=event.model_dump(mode="json")
            )
        )
        await c.execute(
            insert(t.grants).values(
                principal_id="alice",
                company_id=profile.company_id,
                payload=grant.model_dump(mode="json"),
            )
        )
    client, ctx = await login()
    try:
        response = await client.patch(
            "/api/v1/me/context",
            json={"expected_version": ctx["version"], "company_id": profile.company_id},
        )
        assert response.status_code == 200, response.text
        result = await finish(
            client,
            await start(client, response.json(), event_id=event.id, temporal_mode="strict_as_of"),
        )
        assert result.bank_evidence_status == "not_found" and not result.sections.bank_view
        assert any(c.origin == "model" for c in result.claims)
        assert all(c.origin != "bank_analysis" for c in result.claims if c.kind != "fact")
        # The model is allowed to propose a conditional external inference without
        # pretending this private corporate fixture is an authoritative bank thesis.
        assert result.delivery.original_mode == "controlled"
    finally:
        await client.aclose()
