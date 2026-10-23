"""Real imported inputs/API/separate worker; controlled LLM is explicitly a template.

Requires prepare_product after prepare_g1, plus the two saved B02 captures.
No paid calls. Conflicting/missing-source cases below are labeled injected fixtures.
"""

from uuid import uuid4

import pytest
from isolation import isolated_scenario
from sqlalchemy import insert, select, update
from test_g1 import finish, login, start

from app.core import tables as t


@pytest.fixture(autouse=True)
async def restore_state(db):
    async with isolated_scenario(db):
        yield


async def switch(client, ctx, company, role="cfo"):
    r = await client.patch(
        "/api/v1/me/context",
        json={"expected_version": ctx["version"], "company_id": company, "role": role},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role_source"] == "user_selected"
    return r.json()


async def test_personalized_feed_does_not_inherit_profile_permission_from_news_read(db):
    client, _ = await login()
    try:
        async with db.begin() as c:
            grant = dict(
                await c.scalar(
                    select(t.grants.c.payload).where(
                        t.grants.c.principal_id == "alice", t.grants.c.company_id == "U_15728"
                    )
                )
            )
            grant["scopes"] = [s for s in grant["scopes"] if s != "profile.read"]
            await c.execute(
                update(t.grants)
                .where(t.grants.c.principal_id == "alice", t.grants.c.company_id == "U_15728")
                .values(payload=grant)
            )
        assert (await client.get("/api/v1/companies/U_15728/profile")).status_code == 404
        # A personalized score/feature value must obey the same profile boundary.
        assert (await client.get("/api/v1/feed")).status_code == 404
        assert (await client.get("/api/v1/news")).status_code == 200
    finally:
        await client.aclose()


async def test_real_personalization_new_rates_and_fuel_with_roles(db):
    client, ctx = await login()
    try:
        feeds = {}
        for company in ["U_15728", "U_11496"]:
            ctx = await switch(client, ctx, company)
            r = await client.get("/api/v1/feed", params={"as_of": "2026-10-22T12:00:00Z"})
            assert r.status_code == 200, r.text
            feeds[company] = r.json()
            assert len(feeds[company]) >= 3
            for item in feeds[company]:
                rel = item["relevance"]
                assert item["feature_ids"] and rel["score"] == sum(
                    x["weight"] for x in rel["reasons"]
                )
                assert all(x["text"] and x["caveat"] for x in rel["reasons"])
                for fid in item["feature_ids"]:
                    assert (await client.get("/api/v1/features/" + fid)).status_code == 200
        assert feeds["U_15728"][0]["relevance"]["channel"] == "rates"
        assert feeds["U_11496"][0]["relevance"]["channel"] == "fuel_costs"
        assert feeds["U_15728"][0]["relevance"]["score"] == 90
        assert feeds["U_11496"][0]["relevance"]["score"] == 60
        # New source-backed forecast event is not a UI/LLM fixture, and never a fact.
        ctx = await switch(client, ctx, "U_15728")
        result = await finish(
            client,
            await start(
                client, ctx, event_id="case-bank-rate-outlook-2027", as_of="2026-10-22T12:00:00Z"
            ),
        )
        assert (
            result.sections.relevance and result.sections.consequences and result.sections.actions
        )
        assert {c.impact_kind for c in result.claims if c.origin == "model"} >= {
            "risk",
            "opportunity",
        }
        assert result.claims[0].kind == "hypothesis" and result.claims[0].origin == "bank_analysis"
        assert result.bank_context.diagnosis == "quality_limited"
        assert result.bank_context.theses and not result.sections.bank_view
        assert all(b.status == "unverified" and b.issued_at for b in result.bank_context.theses)
        assert "bank_availability_unknown" in result.bank_context.gaps
        for thesis in result.bank_context.theses:
            assert thesis.text == next(
                e.quote for e in result.evidence if e.id in thesis.evidence_ids
            )
        ctx = await switch(client, ctx, "U_15728", "ceo")
        ceo = await finish(
            client, await start(client, ctx, event_id=result.event_id, as_of="2026-10-22T12:00:00Z")
        )
        assert ceo.questions != result.questions
        assert ceo.claims[0].text == result.claims[0].text
        assert ceo.bank_context == result.bank_context
        assert [c.text for c in ceo.claims if c.id in ceo.sections.actions] != [
            c.text for c in result.claims if c.id in result.sections.actions
        ]
        # Actual saved Rosstat article, selected by meaning rather than demo event ID.
        news = (await client.get("/api/v1/news")).json()
        fuel = next(e for e in news if "нефтепродукт" in e["title"].casefold())
        fuel_result = await finish(
            client, await start(client, ctx, event_id=fuel["id"], as_of="2026-10-22T12:00:00Z")
        )
        assert fuel_result.sections.actions and fuel_result.sections.consequences
        assert any(c.origin == "rule" and c.feature_ids for c in fuel_result.claims)
        assert fuel_result.bank_context.theses and all(
            b.status == "unverified" for b in fuel_result.bank_context.theses
        )
        assert all(c.kind == "hypothesis" for c in fuel_result.claims if c.origin == "model")
        ctx = await switch(client, ctx, "U_11496")
        missing = await finish(client, await start(client, ctx))
        assert (
            missing.usage_status == "not_called" and "financing_exposure" in missing.missing_fields
        )
        assert not any(c.origin == "model" for c in missing.claims)
        strict = await finish(client, await start(client, ctx, temporal_mode="strict_as_of"))
        assert strict.bank_context.diagnosis == "excluded_by_time"
        assert not strict.bank_context.theses and strict.usage_status == "not_called"
    finally:
        await client.aclose()


@pytest.mark.parametrize("conflict", [False, True])
async def test_injected_insufficient_or_conflicting_source_stops_before_model(db, conflict):
    """Inject separate temporary IDs; never edit an original evidence/source version."""
    client, ctx = await login()
    try:
        async with db.begin() as c:
            event = dict(
                await c.scalar(
                    select(t.events.c.payload).where(t.events.c.id == "case-rate-20260911")
                )
            )
            event["id"] = "test_product_" + uuid4().hex
            event["title"] = (
                "Injected source-conflict fixture"
                if conflict
                else "Injected missing-source fixture"
            )
            original = dict(
                await c.scalar(
                    select(t.evidence.c.payload).where(t.evidence.c.id == event["evidence_ids"][0])
                )
            )
            event["evidence_ids"] = []
            if conflict:
                original["id"] = event["id"] + "_evidence"
                original["quality"] = "conflict"
                await c.execute(
                    insert(t.evidence).values(
                        id=original["id"],
                        document_id=original["document_version_id"],
                        payload=original,
                    )
                )
                event["evidence_ids"] = [original["id"]]
            await c.execute(
                insert(t.events).values(
                    id=event["id"], source_id=event["source_version_id"], payload=event
                )
            )
        result = await finish(client, await start(client, ctx, event_id=event["id"]))
        assert result.status == "insufficient_evidence" and result.usage_status == "not_called"
        assert not result.claims
        assert (
            "source_conflict" if conflict else "event_evidence_missing"
        ) in result.missing_fields
    finally:
        await client.aclose()
