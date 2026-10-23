"""Controlled DB regressions. Real OCR/E5 run is scripts.verify_ml_corpus."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.auth.service import current_context
from app.core import tables as t
from app.core.errors import AppError
from app.ingestion.pdf.pipeline import document_id
from app.ingestion.pdf.store import IMPORT_VERSION, publish_corpus
from app.retrieval.service import HybridRetriever, LocalE5Encoder, RetrievalPolicy
from contracts.v1 import AnalysisRequest, Document, Evidence, SourceRef, SourceVersion

pytestmark = pytest.mark.integration


class ConnectionEngine:
    def __init__(self, c):
        self.c = c

    @asynccontextmanager
    async def begin(self):
        yield self.c

    connect = begin


@pytest.fixture
async def bank(db, monkeypatch):
    config = {
        "documents": {
            "controlled.pdf": {
                "doc_id": "controlled_bank",
                "sha256": "e" * 64,
                "is_historical": True,
            }
        },
        "verified_spans": [],
    }
    monkeypatch.setattr("app.retrieval.service.load_corpus_config", lambda: config)
    did = document_id(config["documents"]["controlled.pdf"], config)
    sv = SourceVersion(
        id="controlled_source_bank",
        domain="case",
        import_version=IMPORT_VERSION,
        ref=SourceRef(
            source_version_id="controlled_source_bank",
            sha256="e" * 64,
            original_filename="controlled.pdf",
        ),
    )
    doc = Document(
        id=did,
        source_version_id=sv.id,
        domain="case",
        title="Controlled test",
        publisher="Alfa-Research",
        published_at="2021-04-15T00:00:00Z",
        available_at=None,
        text="ставка кредит тест",
    )
    ev = Evidence(
        id="controlled_ev_bank",
        document_version_id=did,
        source_version_id=sv.id,
        domain="case",
        quote=doc.text,
        span="page_chars:0:18",
        page=1,
        available_at=None,
        value_kind="unknown",
        quality="unverified",
    )
    async with db.connect() as c:
        txn = await c.begin()
        try:
            wrapped = ConnectionEngine(c)
            await publish_corpus(wrapped, [(sv, doc, [ev], [])], LocalE5Encoder(force_disable=True))
            yield c, wrapped, ev
        finally:
            await txn.rollback()


async def test_shared_bank_scope_server_acl_strict_and_bound_historical_policy(bank):
    c, engine, ev = bank
    for actor in ("alice", "bob"):
        ctx = await current_context(c, actor)
        req = AnalysisRequest(
            event_id="fixture_event",
            context_version=ctx.version,
            as_of=datetime(2026, 10, 22, tzinfo=UTC),
            temporal_mode="snapshot_demo",
        )
        default = HybridRetriever(engine, LocalE5Encoder(force_disable=True))
        assert await default.search(ctx, req, "ставка") == []
        historical = HybridRetriever(
            engine, LocalE5Encoder(force_disable=True), policy=RetrievalPolicy(True)
        )
        assert [e.id for e in await historical.search(ctx, req, "ставка")] == [ev.id]
        strict = req.model_copy(
            update={
                "temporal_mode": __import__(
                    "contracts.v1", fromlist=["TemporalMode"]
                ).TemporalMode.strict_as_of
            }
        )
        assert await historical.search(ctx, strict, "ставка") == []
        forged = ctx.model_copy(update={"version": ctx.version + 1})
        with pytest.raises(AppError) as stale:
            await historical.search(forged, req, "ставка")
        assert stale.value.status == 409
    ctx = await current_context(c, "alice")
    row = (
        await c.execute(
            select(t.grants.c.payload).where(
                t.grants.c.principal_id == "alice", t.grants.c.company_id == ctx.company_id
            )
        )
    ).scalar_one()
    row["scopes"] = [s for s in row["scopes"] if s != "sources.read"]
    await c.execute(
        update(t.grants)
        .where(t.grants.c.principal_id == "alice", t.grants.c.company_id == ctx.company_id)
        .values(payload=row)
    )
    with pytest.raises(AppError) as denied:
        await historical.search(ctx, req, "ставка")
    assert denied.value.status == 404


async def test_missing_manifest_membership_is_not_public(bank):
    c, engine, ev = bank
    ctx = await current_context(c, "alice")
    req = AnalysisRequest(
        event_id="fixture_event",
        context_version=ctx.version,
        as_of="2026-10-22T00:00:00Z",
        temporal_mode="snapshot_demo",
    )
    await c.execute(
        update(t.source_versions)
        .where(t.source_versions.c.id == ev.source_version_id)
        .values(import_version="not_bank_corpus")
    )
    result = await HybridRetriever(
        engine, LocalE5Encoder(force_disable=True), policy=RetrievalPolicy(True)
    ).search(ctx, req, "ставка")
    assert not result
