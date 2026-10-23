"""Stored bank documents and arbitrary private documents have separate ACL paths."""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, update

from app.core import tables as t
from app.ingestion.pdf.store import IMPORT_VERSION
from contracts.v1 import CompanyProfile, Document, Event, Evidence, SourceRef, SourceVersion

pytestmark = pytest.mark.integration


async def test_bank_source_page_span_provenance_escaping_and_private_feed_acl(db, monkeypatch):
    import app.main as main

    token = uuid4().hex
    sid = "source_" + token
    did = "bank_" + token
    eid = "fragment_" + token
    sv = SourceVersion(
        id=sid,
        domain="case",
        import_version=IMPORT_VERSION,
        ref=SourceRef(
            source_version_id=sid, original_filename="controlled-test.pdf", sha256=token * 2
        ),
    )
    quote = "Ставка без изменения. <script>unsafe()</script>"
    doc = Document(
        id=did,
        source_version_id=sid,
        domain="case",
        title="Controlled bank source",
        publisher="Alfa-Research",
        published_at=None,
        available_at=None,
        text="[Стр. 1]\n" + quote,
    )
    ev = Evidence(
        id=eid,
        source_version_id=sid,
        document_version_id=did,
        domain="case",
        page=1,
        quote=quote,
        span=f"page_chars:0:{len(quote)}",
        value_kind="unknown",
        quality="unverified",
        available_at=None,
    )
    scope = CompanyProfile(
        company_id="scope_" + token,
        snapshot_id="scope_snapshot_" + token,
        source_version_id=sid,
        name="Storage scope",
        industry=None,
        client_type="unknown",
        status="stub",
        features=[],
    )
    # Use the actual is_bank_document predicate with controlled approved ID/scope.
    monkeypatch.setattr("app.ingestion.pdf.store.corpus_document_ids", lambda config=None: [did])
    monkeypatch.setattr("app.ingestion.pdf.store.PUBLIC_SCOPE", scope.company_id)
    async with db.connect() as c:
        transaction = await c.begin()

        class Wrapper:
            @asynccontextmanager
            async def connect(self):
                yield c

            begin = connect

        monkeypatch.setattr(main, "engine", Wrapper())
        try:
            await c.execute(
                insert(t.source_versions).values(
                    id=sid,
                    sha256=sv.ref.sha256,
                    import_version=IMPORT_VERSION,
                    payload=sv.model_dump(mode="json"),
                )
            )
            await c.execute(
                insert(t.companies).values(
                    id=scope.snapshot_id,
                    company_id=scope.company_id,
                    source_id=sid,
                    payload=scope.model_dump(mode="json"),
                )
            )
            await c.execute(
                insert(t.documents).values(
                    id=did,
                    source_id=sid,
                    company_id=scope.company_id,
                    payload=doc.model_dump(mode="json"),
                )
            )
            await c.execute(
                insert(t.evidence).values(
                    id=eid, document_id=did, payload=ev.model_dump(mode="json")
                )
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                assert (await client.get("/api/v1/evidence/" + eid)).status_code == 401
                session = (
                    await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
                ).json()
                client.headers["X-CSRF-Token"] = session["csrf_token"]
                assert (await client.get("/api/v1/sources/" + did)).status_code == 200
                assert (await client.get("/api/v1/evidence/" + eid)).status_code == 200
                assert (await client.get("/api/v1/sources/" + did + "/provenance")).json()["ref"][
                    "sha256"
                ] == sv.ref.sha256
                rendered = await client.get("/evidence/" + eid)
                assert (
                    rendered.status_code == 200
                    and "&lt;script&gt;" in rendered.text
                    and "<script>" not in rendered.text
                )
                # Tampering with a page/span or quote is a confirmed data conflict.
                await c.execute(
                    update(t.evidence)
                    .where(t.evidence.c.id == eid)
                    .values(payload={**ev.model_dump(mode="json"), "page": 99})
                )
                assert (await client.get("/api/v1/evidence/" + eid)).status_code == 409
                await c.execute(
                    update(t.evidence)
                    .where(t.evidence.c.id == eid)
                    .values(payload=ev.model_dump(mode="json"))
                )
                # Removing bank registry membership does not make this non-client scope public.
                monkeypatch.setattr(
                    "app.ingestion.pdf.store.corpus_document_ids", lambda config=None: []
                )
                event = Event(
                    id="private_" + token,
                    source_version_id=sid,
                    domain="case",
                    title="PRIVATE TITLE",
                    event_at="2026-09-11T00:00:00Z",
                    available_at=None,
                    evidence_ids=[eid],
                )
                await c.execute(
                    insert(t.events).values(
                        id=event.id, source_id=sid, payload=event.model_dump(mode="json")
                    )
                )
                assert (await client.get("/api/v1/sources/" + did)).status_code == 404
                feed = await client.get("/api/v1/feed")
                assert feed.status_code == 200 and "PRIVATE TITLE" not in feed.text
        finally:
            await transaction.rollback()
