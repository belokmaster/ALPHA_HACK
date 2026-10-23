from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, insert, select

from app.core import tables as t
from app.ingestion.news.fetch import SafeFetcher
from app.ingestion.news.parse import FEEDS
from app.ingestion.news.service import recent_events, stale_reasons, sync_source
from app.ingestion.news.tables import items, sources, versions
from contracts.examples import examples
from contracts.v1 import Analysis, Document, Event, Evidence

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/news"
NOW = datetime(2026, 10, 22, tzinfo=UTC)


class FixtureSource:
    def __init__(self, key="cbr-press"):
        self.key = key
        self.prefix = "rosstat" if key == "rosstat" else "cbr"
        self.token = uuid4().hex
        self.feed = (
            (FIXTURES / (self.prefix + ".xml"))
            .read_bytes()
            .replace(b"offline.htm", (self.token + ".htm").encode())
            .replace(b"offline-guid-1", self.token.encode())
        )
        self.html = (FIXTURES / (self.prefix + ".html")).read_bytes()
        self.offline = False

    async def fetcher(self):
        async def resolve(_):
            return ["93.184.216.34"]

        def handler(req):
            if self.offline:
                raise httpx.ConnectError("offline fixture")
            feed = req.url.path == urlsplit(FEEDS[self.key][2]).path
            return httpx.Response(
                200,
                headers={"content-type": "application/rss+xml" if feed else "text/html"},
                stream=httpx.ByteStream(self.feed if feed else self.html),
            )

        return SafeFetcher(resolver=resolve, transport=httpx.MockTransport(handler))

    async def sync(self, db):
        return await sync_source(db, self.key, fetcher=await self.fetcher(), clock=lambda: NOW)


async def news_doc(db, token):
    async with db.connect() as c:
        return (
            (
                await c.execute(
                    select(t.documents.c.payload)
                    .join(versions, versions.c.document_id == t.documents.c.id)
                    .join(items, items.c.id == versions.c.item_id)
                    .where(items.c.canonical_url.contains(token))
                    .order_by(versions.c.sequence.desc())
                )
            )
            .scalars()
            .first()
        )


async def test_repeat_then_article_correction_preserves_versions_and_stales_dependencies(db):
    fixture = FixtureSource()
    first = await fixture.sync(db)
    assert first.status == "ok" and first.imported == 1
    old = Document.model_validate(await news_doc(db, fixture.token))
    repeat = await fixture.sync(db)
    assert repeat.imported == 0 and repeat.unchanged == 1
    async with db.connect() as c:
        old_version = (
            (await c.execute(select(versions).where(versions.c.document_id == old.id)))
            .mappings()
            .one()
        )
        ev = Evidence.model_validate(
            (
                await c.execute(
                    select(t.evidence.c.payload).where(t.evidence.c.document_id == old.id)
                )
            )
            .scalars()
            .first()
        )
    assert ev.value_kind == "unknown" and ev.quality == "unverified"
    raw = next(x for x in examples() if isinstance(x, Analysis)).model_dump(mode="json")
    jid = uuid4().hex
    raw.update(
        id=jid,
        job_id=jid,
        domain="live",
        event_id=old_version["event_id"],
        source_version_ids=[old.source_version_id],
        evidence=[ev.model_dump(mode="json")],
    )
    raw["claims"][0]["evidence_ids"] = [ev.id]
    analysis = Analysis.model_validate(raw)
    async with db.begin() as c:
        await c.execute(
            insert(t.jobs).values(
                id=jid,
                principal_id="alice",
                company_id="demo_a",
                kind="analysis",
                route="news-test",
                idempotency_key=jid,
                payload_hash="0" * 64,
                status="succeeded",
                created_at=NOW,
                payload={},
            )
        )
        await c.execute(
            insert(t.analyses).values(
                id=jid,
                job_id=jid,
                principal_id="alice",
                company_id="demo_a",
                payload=analysis.model_dump(mode="json"),
            )
        )
    fixture.html = (FIXTURES / "cbr-corrected.html").read_bytes()
    corrected = await fixture.sync(db)
    assert corrected.imported == 1
    new = Document.model_validate(await news_doc(db, fixture.token))
    assert new.id != old.id and new.supersedes_id == old.id
    async with db.connect() as c:
        preserved = (
            await c.execute(select(t.documents.c.payload).where(t.documents.c.id == old.id))
        ).scalar_one()
        saved = Analysis.model_validate(
            (
                await c.execute(select(t.analyses.c.payload).where(t.analyses.c.id == jid))
            ).scalar_one()
        )
        assert preserved == old.model_dump(mode="json")
        assert saved.delivery.stale and saved.delivery.stale_reasons
        # A late result pinned before correction is detected again at read time.
        assert await stale_reasons(c, analysis)
        visible = [Event.model_validate(x) for x in await recent_events(c)]
        assert old_version["event_id"] not in [x.id for x in visible]
    # Reversion creates a third version instead of resurrecting the first immutable record.
    fixture.html = (FIXTURES / "cbr.html").read_bytes()
    await fixture.sync(db)
    third = Document.model_validate(await news_doc(db, fixture.token))
    assert third.id not in (old.id, new.id) and third.supersedes_id == new.id


async def test_outage_or_invalid_date_preserves_cached_news_and_last_success(db):
    fixture = FixtureSource()
    success = await fixture.sync(db)
    old = await news_doc(db, fixture.token)
    fixture.offline = True
    failed = await fixture.sync(db)
    assert failed.status == "error" and failed.last_success_at == success.last_success_at
    assert await news_doc(db, fixture.token) == old
    fixture.offline = False
    fixture.feed = fixture.feed.replace(b"Wed, 21 Oct 2026 12:00:00 +0300", b"invalid-date")
    bad = await fixture.sync(db)
    assert bad.rejected == 1 and bad.error == "invalid_date"
    assert bad.last_success_at == success.last_success_at
    assert await news_doc(db, fixture.token) == old


async def test_rosstat_rss_article_pipeline_uses_same_contracts(db):
    fixture = FixtureSource("rosstat")
    result = await fixture.sync(db)
    assert result.status == "ok"
    async with db.connect() as c:
        rows = await recent_events(c, source="rosstat")
        event = Event.model_validate(rows[0])
        assert event.domain == "live" and event.event_time_source == "publication"
        state = (
            await c.execute(select(sources.c.payload).where(sources.c.id == "rosstat"))
        ).scalar_one()
        assert state["last_success_at"]


async def test_public_news_source_access_still_requires_server_session_and_scope(db, monkeypatch):
    import app.main as main
    from app.main import app

    monkeypatch.setattr(main, "engine", db)
    fixture = FixtureSource()
    await fixture.sync(db)
    doc = Document.model_validate(await news_doc(db, fixture.token))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        assert (await client.get("/api/v1/sources/" + doc.id)).status_code in (401, 403)
        logged = await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
        assert logged.status_code == 200
        response = await client.get("/api/v1/sources/" + doc.id)
        assert response.status_code == 200 and response.json()["url"] == doc.url
        assert response.headers["content-type"].startswith("application/json")
        listing = await client.get("/api/v1/news")
        event = next(e for e in listing.json() if e["source_version_id"] == doc.source_version_id)
        assert (await client.get("/api/v1/evidence/" + event["evidence_ids"][0])).status_code == 200
        orphan = doc.model_copy(update={"id": "orphan_" + uuid4().hex})
        async with db.begin() as c:
            await c.execute(
                insert(t.documents).values(
                    id=orphan.id,
                    source_id=doc.source_version_id,
                    company_id=None,
                    payload=orphan.model_dump(mode="json"),
                )
            )
        assert (await client.get("/api/v1/sources/" + orphan.id)).status_code == 404
        assert (await client.get("/api/v1/sources/demo_hidden_doc")).status_code == 404
        assert (await client.get("/api/v1/news/sources")).status_code == 200
        listing = await client.get("/api/v1/news")
        assert listing.status_code == 200 and listing.json()


async def test_unsafe_redirect_does_not_publish_any_source_version(db):
    before = None
    async with db.connect() as c:
        before = (await c.execute(select(func.count()).select_from(versions))).scalar_one()

    async def resolver(_):
        return ["93.184.216.34"]

    seen = []

    def redirect(req):
        seen.append(str(req.url))
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, stream=httpx.ByteStream(b"")
        )

    state = await sync_source(
        db,
        "cbr-events",
        fetcher=SafeFetcher(resolver=resolver, transport=httpx.MockTransport(redirect)),
        clock=lambda: NOW,
    )
    assert state.status == "error" and state.error == "unsafe_url" and len(seen) == 1
    async with db.connect() as c:
        assert (await c.execute(select(func.count()).select_from(versions))).scalar_one() == before


async def test_guid_alias_survives_unchanged_text_and_url_change(db):
    fixture = FixtureSource()
    await fixture.sync(db)
    new_guid = uuid4().hex
    # Keep URL stable while changing only GUID, then move URL under the same new GUID.
    original = (FIXTURES / "cbr.xml").read_bytes()
    fixture.feed = original.replace(b"offline.htm", (fixture.token + ".htm").encode()).replace(
        b"offline-guid-1", new_guid.encode()
    )
    unchanged = await fixture.sync(db)
    assert unchanged.unchanged == 1
    new_url_token = uuid4().hex
    fixture.feed = fixture.feed.replace(
        (fixture.token + ".htm").encode(), (new_url_token + ".htm").encode()
    )
    moved = await fixture.sync(db)
    assert moved.imported == 1
    repeat = await fixture.sync(db)
    assert repeat.unchanged == 1
    async with db.connect() as c:
        row = (
            (await c.execute(select(items).where(items.c.known_guids.contains([new_guid]))))
            .mappings()
            .one()
        )
        assert fixture.token in row["known_guids"] and new_guid in row["known_guids"]
        count = (
            await c.execute(
                select(func.count()).select_from(versions).where(versions.c.item_id == row["id"])
            )
        ).scalar_one()
        assert count == 2


async def test_cancelled_fetch_releases_source_lock_for_another_process_pool(db):
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    entered = asyncio.Event()

    async def resolve(_):
        return ["93.184.216.34"]

    async def blocked(_):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        sync_source(
            db,
            "cbr-events",
            fetcher=SafeFetcher(resolver=resolve, transport=httpx.MockTransport(blocked)),
            clock=lambda: NOW,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    other = create_async_engine(db.url)
    try:
        result = await FixtureSource("cbr-events").sync(other)
        assert result.status == "ok"
    finally:
        await other.dispose()
