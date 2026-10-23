"""M04 production adapter tested against actual PostgreSQL ACL/time metadata."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import insert, update

from app.agent.events import DBEventProvider
from app.auth.service import current_context
from app.core import tables as t
from app.core.errors import AppError
from contracts.v1 import AnalysisRequest, Document, Event, Evidence, SourceRef, SourceVersion


@pytest.mark.parametrize(
    "gap", ["none", "source_unknown", "document_future", "evidence_unknown", "private", "bad_span"]
)
async def test_evidence_is_authorized_and_filtered_before_model_exposure(db, gap):
    stamp = "2026-09-01T00:00:00Z"
    sid = "test_" + uuid4().hex
    source = SourceVersion(
        id=sid,
        domain="case",
        available_at=None if gap == "source_unknown" else stamp,
        ref=SourceRef(
            source_version_id=sid, original_filename="injected-fixture.txt", sha256=uuid4().hex * 2
        ),
    )
    doc = Document(
        id=sid + "_doc",
        source_version_id=sid,
        domain="case",
        title="Injected fixture, not real evidence",
        publisher="Test only",
        published_at=stamp,
        available_at="2027-01-01T00:00:00Z" if gap == "document_future" else stamp,
        text="Controlled source text.",
    )
    ev = Evidence(
        id=sid + "_ev",
        document_version_id=doc.id,
        source_version_id=sid,
        domain="case",
        quote=doc.text,
        span=f"chars:0:{len(doc.text)}" if gap != "bad_span" else "chars:0:1",
        quality="unverified",
        value_kind="fact",
        available_at=None if gap == "evidence_unknown" else stamp,
    )
    event = Event(
        id=sid + "_event",
        source_version_id=sid,
        domain="case",
        title=doc.title,
        event_at=stamp,
        available_at=stamp,
        evidence_ids=[ev.id],
        topics=["rates"],
    )
    async with db.connect() as c:
        transaction = await c.begin()

        class BoundConnection:
            @asynccontextmanager
            async def connect(self):
                yield c

        try:
            await c.execute(
                insert(t.source_versions).values(
                    id=sid,
                    sha256=source.ref.sha256,
                    import_version=source.import_version,
                    payload=source.model_dump(mode="json"),
                )
            )
            await c.execute(
                insert(t.documents).values(
                    id=doc.id,
                    source_id=sid,
                    company_id="demo_hidden" if gap == "private" else "demo_a",
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
            ctx = await current_context(c, "alice")
            # Context is seeded on demo_a; no caller-created grant or company switch.
            provider = DBEventProvider(BoundConnection())
            req = AnalysisRequest(
                event_id=event.id,
                context_version=ctx.version,
                as_of="2026-09-14T00:00:00Z",
                temporal_mode="strict_as_of",
            )
            if gap in {"private", "bad_span"}:
                with pytest.raises(AppError):
                    await provider.get_evidence_at(ctx, req, [ev.id])
                with pytest.raises(AppError):
                    await provider.get_event(ctx, event.id)
            else:
                result = await provider.get_evidence_at(ctx, req, [ev.id])
                assert [e.id for e in result] == ([ev.id] if gap == "none" else [])
                if gap == "none":
                    # A scope/version change invalidates the adapter, even with a cached ctx.
                    await c.execute(
                        update(t.principals)
                        .where(t.principals.c.id == "alice")
                        .values(acl_version=ctx.acl_version + 1)
                    )
                    with pytest.raises(AppError):
                        await provider.get_evidence_at(ctx, req, [ev.id])
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("quality,future", [("verified", False), ("unverified", False), ("verified", True)])
async def test_bank_position_requires_review_and_eligible_version(db, monkeypatch, quality, future):
    from app.agent.providers import BankSearch
    from app.ingestion.pdf import store
    from contracts.v1 import CompanyProfile

    sid = "bank_" + uuid4().hex
    stamp = "2026-09-01T00:00:00Z"
    source = SourceVersion(id=sid, domain="case", available_at=stamp, import_version=store.IMPORT_VERSION,
                           ref=SourceRef(source_version_id=sid, original_filename="test-bank.txt", sha256="a" * 64))
    doc = Document(id=sid + "_doc", source_version_id=sid, domain="case", title="Fixture",
                   publisher="Fixture bank", published_at="2027-01-01T00:00:00Z" if future else stamp,
                   available_at=stamp, text="Банк ожидает снижения ставки.")
    ev = Evidence(id=sid + "_ev", document_version_id=doc.id, source_version_id=sid, domain="case",
                  quote=doc.text, span=f"chars:0:{len(doc.text)}", quality=quality,
                  value_kind="forecast", available_at=stamp)
    monkeypatch.setattr(store, "corpus_document_ids", lambda: [doc.id])
    async with db.connect() as c:
        tx = await c.begin()

        class Bound:
            @asynccontextmanager
            async def connect(self):
                yield c

        try:
            await c.execute(insert(t.source_versions).values(id=sid, sha256=source.ref.sha256,
                            import_version=source.import_version, payload=source.model_dump(mode="json")))
            sentinel = CompanyProfile(company_id=store.PUBLIC_SCOPE, snapshot_id=sid + "_snapshot",
                                      source_version_id=sid, name="Fixture corpus", industry=None,
                                      client_type="unknown", status="stub", features=[])
            await c.execute(insert(t.companies).values(id=sentinel.snapshot_id,
                            company_id=store.PUBLIC_SCOPE, source_id=sid, payload=sentinel.model_dump(mode="json")))
            await c.execute(insert(t.documents).values(id=doc.id, source_id=sid,
                            company_id=store.PUBLIC_SCOPE, payload=doc.model_dump(mode="json")))
            await c.execute(insert(t.evidence).values(id=ev.id, document_id=doc.id,
                            payload=ev.model_dump(mode="json")))
            ctx = await current_context(c, "alice")
            req = AnalysisRequest(event_id="fixture", context_version=ctx.version,
                                  as_of="2026-09-14T00:00:00Z", temporal_mode="strict_as_of")
            result = await BankSearch(Bound(), None).describe_bank(ctx, req, "rates", [ev.id], True)
            if future:
                assert result.diagnosis == "excluded_by_time" and not result.theses
            else:
                assert result.theses[0].text == doc.text
                assert result.theses[0].status == ("applicable" if quality == "verified" else "unverified")
                assert result.diagnosis == ("available" if quality == "verified" else "quality_limited")
        finally:
            await tx.rollback()
