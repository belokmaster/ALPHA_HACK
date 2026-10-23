import asyncio
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import tables as t
from app.ingestion.news.fetch import NewsError, SafeFetcher, safe_url
from app.ingestion.news.parse import FEEDS, PARSER_VERSION, article, rss
from app.ingestion.news.tables import items, sources, versions
from contracts.v1 import Document, Event, Evidence, NewsSourceState, SourceRef, SourceVersion


def digest(value):
    return hashlib.sha256(value).hexdigest()


def dependencies(analysis):
    source_ids = set(analysis.source_version_ids + analysis.temporal.depends_on_source_versions)
    source_ids.update(e.source_version_id for e in analysis.evidence)
    return source_ids, {e.document_version_id for e in analysis.evidence}


async def stale_reasons(c, analysis):
    source_ids, doc_ids = dependencies(analysis)
    rows = (
        (
            await c.execute(
                select(versions.c.source_id)
                .join(items, versions.c.item_id == items.c.id)
                .where(
                    versions.c.document_id != items.c.current_document_id,
                    or_(
                        versions.c.source_id.in_(source_ids),
                        versions.c.document_id.in_(doc_ids),
                        versions.c.event_id == analysis.event_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return ["news_source_corrected:" + source_id for source_id in sorted(set(rows))]


async def publish(engine, publisher, parsed, snapshot, observed_at):
    fingerprint = digest(
        json.dumps(
            {
                "url": parsed.url,
                "title": parsed.title,
                "text": parsed.text,
                "published_at": parsed.published_at.isoformat(),
                "precision": parsed.precision,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    )
    item_id = "news_" + digest((publisher + parsed.url).encode())[:32]
    async with engine.begin() as c:
        lock_key = int.from_bytes(
            hashlib.sha256(("news:" + publisher).encode()).digest()[:8], "big", signed=True
        )
        await c.execute(select(func.pg_advisory_xact_lock(lock_key)))
        matches = [items.c.canonical_url == parsed.url]
        if parsed.guid:
            matches.append(items.c.known_guids.contains([parsed.guid]))
        existing = (
            (
                await c.execute(
                    select(items)
                    .where(items.c.publisher == publisher, or_(*matches))
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        if len(existing) > 1:
            raise NewsError("identity_conflict")
        if existing:
            row = existing[0]
            item_id = row["id"]
        else:
            await c.execute(
                insert(items).values(
                    id=item_id, publisher=publisher, canonical_url=parsed.url, known_guids=[]
                )
            )
            row = (await c.execute(select(items).where(items.c.id == item_id))).mappings().one()
        guids = list(row["known_guids"])
        if parsed.guid and parsed.guid not in guids:
            if len(guids) >= 100:
                raise NewsError("identity_alias_limit")
            guids.append(parsed.guid)
            await c.execute(update(items).where(items.c.id == item_id).values(known_guids=guids))
        old = None
        if row["current_document_id"]:
            old = (
                (
                    await c.execute(
                        select(versions).where(versions.c.document_id == row["current_document_id"])
                    )
                )
                .mappings()
                .one()
            )
            if old["normalized_sha256"] == fingerprint:
                return "unchanged", old["document_id"]
        seq = old["sequence"] + 1 if old else 1
        # Revision sequence distinguishes A -> B -> A without overwriting the first A.
        version_hash = digest((item_id + ":" + str(seq) + ":" + fingerprint).encode())[:32]
        source_id, doc_id, event_id = (
            "news_src_" + version_hash,
            "news_doc_" + version_hash,
            "news_event_" + version_hash,
        )
        ref = SourceRef(
            source_version_id=source_id,
            original_filename=parsed.url,
            sha256=digest(snapshot.body),
            parser_version=PARSER_VERSION,
            rule_version="news-topics-1",
        )
        source = SourceVersion(
            id=source_id,
            ref=ref,
            domain="live",
            available_at=observed_at,
            import_version="news-1:" + item_id[5:21] + ":" + str(seq),
        )
        document = Document(
            id=doc_id,
            source_version_id=source_id,
            domain="live",
            title=parsed.title,
            publisher=FEEDS[next(k for k, v in FEEDS.items() if v[0] == publisher)][1],
            published_at=parsed.published_at,
            available_at=observed_at,
            text=parsed.text,
            url=parsed.url,
            guid=parsed.guid,
            supersedes_id=old["document_id"] if old else None,
            date_precision=parsed.precision,
        )
        evidence = [
            Evidence(
                id=doc_id + "_" + str(i),
                document_version_id=doc_id,
                source_version_id=source_id,
                quote=parsed.text[start : start + 2000],
                span=f"chars:{start}:{min(start + 2000, len(parsed.text))}",
                value_kind="unknown",
                quality="unverified",
                domain="live",
                available_at=observed_at,
            )
            for i, start in enumerate(range(0, len(parsed.text), 2000))
        ]
        event = Event(
            id=event_id,
            source_version_id=source_id,
            domain="live",
            title=parsed.title,
            available_at=observed_at,
            event_at=parsed.published_at,
            evidence_ids=[e.id for e in evidence],
            topics=parsed.topics,
            event_time_source="publication",
        )
        await c.execute(
            insert(t.source_versions).values(
                id=source_id,
                sha256=ref.sha256,
                import_version=source.import_version,
                payload=source.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.documents).values(
                id=doc_id,
                source_id=source_id,
                company_id=None,
                payload=document.model_dump(mode="json"),
            )
        )
        await c.execute(
            insert(t.events).values(
                id=event_id, source_id=source_id, payload=event.model_dump(mode="json")
            )
        )
        await c.execute(
            insert(t.evidence),
            [
                {"id": e.id, "document_id": doc_id, "payload": e.model_dump(mode="json")}
                for e in evidence
            ],
        )
        await c.execute(
            insert(versions).values(
                document_id=doc_id,
                event_id=event_id,
                source_id=source_id,
                item_id=item_id,
                sequence=seq,
                normalized_sha256=fingerprint,
                guid=parsed.guid,
                fetched_at=observed_at,
            )
        )
        await c.execute(
            update(items)
            .where(items.c.id == item_id)
            .values(current_document_id=doc_id, canonical_url=parsed.url)
        )
        if old:
            # Existing results stay readable, including their old evidence/snapshot.
            matches = or_(
                t.analyses.c.payload["event_id"].astext == old["event_id"],
                t.analyses.c.payload["source_version_ids"].contains([old["source_id"]]),
                t.analyses.c.payload["temporal"]["depends_on_source_versions"].contains(
                    [old["source_id"]]
                ),
                t.analyses.c.payload["evidence"].contains(
                    [{"source_version_id": old["source_id"]}]
                ),
                t.analyses.c.payload["evidence"].contains(
                    [{"document_version_id": old["document_id"]}]
                ),
            )
            results = (
                (await c.execute(select(t.analyses).where(matches).with_for_update()))
                .mappings()
                .all()
            )
            for result in results:
                payload = dict(result["payload"])
                delivery = dict(payload["delivery"])
                delivery["stale"] = True
                delivery["stale_reasons"] = list(
                    dict.fromkeys(
                        delivery.get("stale_reasons", [])
                        + ["news_source_corrected:" + old["source_id"]]
                    )
                )
                payload["delivery"] = delivery
                await c.execute(
                    update(t.analyses)
                    .where(t.analyses.c.id == result["id"])
                    .values(payload=payload)
                )
        return "imported", doc_id


async def sync_source(
    engine, source_key, *, limit=5, fetcher=None, clock=lambda: datetime.now(UTC)
):
    if source_key not in FEEDS or not 1 <= limit <= 50:
        raise ValueError("Known source and limit 1..50 required")
    fetcher = fetcher or SafeFetcher()
    publisher, _, url = FEEDS[source_key]
    # Session advisory lock serializes an entire source sync without a long transaction.
    # Pin a dedicated connection in autocommit, never a SQL transaction across HTTP.
    lock_key = int.from_bytes(hashlib.sha256(source_key.encode()).digest()[:8], "big", signed=True)
    async with engine.connect() as lock:
        lock = await lock.execution_options(isolation_level="AUTOCOMMIT")
        if not (await lock.execute(select(func.pg_try_advisory_lock(lock_key)))).scalar_one():
            raise NewsError("source_busy")
        try:
            async with engine.connect() as c:
                prior = (
                    await c.execute(select(sources.c.payload).where(sources.c.id == source_key))
                ).scalar_one_or_none()
            state = (
                NewsSourceState.model_validate(prior)
                if prior
                else NewsSourceState(id=source_key, url=url)
            )
            state.last_attempt_at = clock()
            state.imported = state.unchanged = state.rejected = 0
            errors = []
            try:
                feed = await fetcher.fetch(url, kind="rss")
                entries = rss(feed.body, feed.url)[:limit]
                seen = set()
                for item in entries:
                    try:
                        link = safe_url(item.url)
                        if (publisher == "cbr") != ("cbr.ru" in link.split("/", 3)[2]):
                            raise NewsError("cross_publisher_item")
                        identity = (item.guid, link)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        if item.guid and len(item.guid) > 1024:
                            raise NewsError("invalid_guid")
                        page = await fetcher.fetch(link, kind="html")
                        now = clock()
                        parsed = article(page, item, now)
                        outcome, _ = await publish(engine, publisher, parsed, page, now)
                        setattr(state, outcome, getattr(state, outcome) + 1)
                    except NewsError as exc:
                        errors.append(exc.code)
                        state.rejected += 1
                if not errors or state.imported + state.unchanged:
                    state.last_success_at = clock()
                state.status = "partial" if errors else "ok"
            except NewsError as exc:
                errors.append(exc.code)
                state.status = "error"
            state.error = ",".join(sorted(set(errors))) or None
            async with engine.begin() as c:
                await c.execute(
                    pg_insert(sources)
                    .values(id=source_key, payload=state.model_dump(mode="json"))
                    .on_conflict_do_update(
                        index_elements=[sources.c.id],
                        set_={"payload": state.model_dump(mode="json")},
                    )
                )
            return state
        finally:
            try:
                await asyncio.shield(lock.execute(select(func.pg_advisory_unlock(lock_key))))
            except BaseException:
                await asyncio.shield(lock.invalidate())
                raise


async def source_states(c):
    stored = {row["id"]: row["payload"] for row in (await c.execute(select(sources))).mappings()}
    return [
        NewsSourceState.model_validate(stored[key])
        if key in stored
        else NewsSourceState(id=key, url=value[2])
        for key, value in FEEDS.items()
    ]


async def is_public(c, document_id):
    return (
        await c.execute(select(versions.c.document_id).where(versions.c.document_id == document_id))
    ).first() is not None


async def recent_events(c, limit=20, source=None):
    query = (
        select(t.events.c.payload)
        .join(versions, t.events.c.id == versions.c.event_id)
        .join(items, versions.c.item_id == items.c.id)
        .where(items.c.current_document_id == versions.c.document_id)
    )
    if source:
        query = query.where(items.c.publisher == source)
    return (
        (
            await c.execute(
                query.order_by(versions.c.fetched_at.desc(), versions.c.event_id).limit(limit)
            )
        )
        .scalars()
        .all()
    )
