"""Read-only adapters; each operation rechecks server context and current grants."""

from datetime import datetime

from sqlalchemy import select

from app import sources
from app.auth.service import authorize, current_context, profile
from app.core import tables as t
from app.core.errors import AppError
from app.ingestion.csv.normalization import assess_time
from contracts.v1 import BankContext, BankThesis, ErrorCode, Event, Evidence, SourceVersion


async def pinned(c, context):
    current = await current_context(c, context.principal_id)
    if any(
        getattr(current, k) != getattr(context, k)
        for k in ("id", "version", "company_id", "role", "preferences", "acl_version")
    ):
        raise AppError(ErrorCode.context_stale, "Pinned context changed", 409)
    await authorize(c, current, context.company_id, "analysis.run")
    return current


class Profiles:
    def __init__(self, engine):
        self.engine = engine

    async def get_profile(self, context, company_id):
        async with self.engine.connect() as c:
            await pinned(c, context)
            return await profile(c, context, company_id)


class Events:
    def __init__(self, engine):
        self.engine = engine

    async def get_event(self, context, event_id):
        async with self.engine.connect() as c:
            await pinned(c, context)
            await authorize(c, context, context.company_id, "news.read")
            payload = (
                await c.execute(select(t.events.c.payload).where(t.events.c.id == event_id))
            ).scalar_one_or_none()
            if payload is None:
                raise AppError(ErrorCode.not_found, "Resource not found", 404)
            event = Event.model_validate(payload)
            for eid in event.evidence_ids:
                await sources.evidence(c, context, eid)
            return event

    async def get_evidence(self, context, ids):
        async with self.engine.connect() as c:
            await pinned(c, context)
            return [await sources.evidence(c, context, eid) for eid in ids]

    async def get_evidence_at(self, context, request, ids):
        """Check every dependency before exposing evidence to any model stage."""
        async with self.engine.connect() as c:
            await pinned(c, context)
            result = []
            for eid in ids:
                ev = await sources.evidence(c, context, eid)
                doc = await sources.document(c, context, ev.document_version_id)
                raw = await c.scalar(
                    select(t.source_versions.c.payload).where(
                        t.source_versions.c.id == ev.source_version_id
                    )
                )
                source = SourceVersion.model_validate(raw)
                stamps = (ev.available_at, doc.available_at, source.available_at)
                if doc.published_at and doc.published_at > request.as_of:
                    continue
                if any(stamp and stamp > request.as_of for stamp in stamps):
                    continue
                if any(
                    assess_time(
                        request.temporal_mode, request.as_of, stamp, None, [source.id]
                    ).temporal_status
                    == "excluded"
                    for stamp in stamps
                ):
                    continue
                result.append(ev)
            return result

    async def origins(self, context, ids):
        from app.ingestion.pdf.store import is_bank_document

        async with self.engine.connect() as c:
            await pinned(c, context)
            result = {}
            for eid in ids:
                ev = await sources.evidence(c, context, eid)
                result[eid] = (
                    "bank_analysis"
                    if await is_bank_document(c, ev.document_version_id)
                    else "external_source"
                )
            return result


class BankSearch:
    """A corporate document matching the query is not automatically bank research."""

    def __init__(self, engine, retriever):
        self.engine, self.retriever = engine, retriever

    async def search(self, context, request, query):
        from app.ingestion.pdf.store import is_bank_document

        items = await self.retriever.search(context, request, query)
        async with self.engine.connect() as c:
            await pinned(c, context)
            items = [e for e in items if await is_bank_document(c, e.document_version_id)]
            # Retrieve complete typed spans from already retrieved pages. Raw OCR
            # chunk boundaries can otherwise separate a forecast's number/horizon.
            # This is page expansion, not another search or promotion of OCR quality.
            pages = {(e.document_version_id, e.page) for e in items}
            if not pages:
                return []
            rows = (
                await c.execute(
                    select(t.evidence.c.payload)
                    .where(
                        t.evidence.c.document_id.in_({did for did, _ in pages}),
                        t.evidence.c.payload["value_kind"].astext != "unknown",
                    )
                    .limit(64)
                )
            ).scalars()
            typed = [Evidence.model_validate(p) for p in rows]
            typed = [
                e
                for e in typed
                if (e.document_version_id, e.page) in pages
                and (
                    (e.available_at is not None and e.available_at <= request.as_of)
                    or (e.available_at is None and request.temporal_mode.value == "snapshot_demo")
                )
            ]
            import re

            pattern = r"ставк" if "ставк" in query else r"топлив|бензин|дизел"
            typed.sort(
                key=lambda e: (
                    not bool(re.search(pattern, e.quote, re.I)),
                    e.value_kind not in {"forecast", "opinion"},
                    e.id,
                )
            )
            return list({e.id: e for e in typed + items}.values())[:8]

    async def describe_bank(self, context, request, channel, evidence_ids, searched):
        import re

        from app.ingestion.csv.normalization import assess_time
        from app.ingestion.pdf.store import corpus_document_ids, is_bank_document

        result = BankContext(diagnosis="not_found")
        async with self.engine.connect() as c:
            await pinned(c, context)
            docs = (
                (
                    await c.execute(
                        select(t.documents.c.payload).where(
                            t.documents.c.id.in_(corpus_document_ids())
                        )
                    )
                )
                .scalars()
                .all()
            )
            source_dates = {
                p["id"]: p.get("available_at")
                for p in (
                    await c.execute(
                        select(t.source_versions.c.payload).where(
                            t.source_versions.c.id.in_({d["source_version_id"] for d in docs})
                        )
                    )
                ).scalars()
            }
            eligible = [
                d
                for d in docs
                if (
                    d["published_at"] is None
                    or datetime.fromisoformat(d["published_at"]) <= request.as_of
                )
                and assess_time(
                    request.temporal_mode,
                    request.as_of,
                    datetime.fromisoformat(d["available_at"]) if d["available_at"] else None,
                    None,
                    [d["source_version_id"]],
                ).temporal_status
                != "excluded"
                and assess_time(
                    request.temporal_mode,
                    request.as_of,
                    datetime.fromisoformat(source_dates[d["source_version_id"]])
                    if source_dates.get(d["source_version_id"])
                    else None,
                    None,
                    [d["source_version_id"]],
                ).temporal_status
                != "excluded"
            ]
            if docs and not eligible:
                return BankContext(
                    diagnosis="excluded_by_time", gaps=["bank_sources_excluded_by_time"]
                )
            if not searched:
                return BankContext(diagnosis="not_available", gaps=["bank_search_not_run"])
            pattern = r"ставк" if channel == "rates" else r"топлив|бензин|дизел"
            for eid in evidence_ids:
                ev = await sources.evidence(c, context, eid)
                if not await is_bank_document(c, ev.document_version_id):
                    continue
                doc = await sources.document(c, context, ev.document_version_id)
                if doc.id not in {d["id"] for d in eligible}:
                    continue
                if ev.value_kind not in {"forecast", "opinion"} or not re.search(
                    pattern, ev.quote, re.I
                ):
                    continue
                # Explicit reviewed evidence permits a dated bank position, not a personal effect.
                result.theses.append(
                    BankThesis(
                        id="context_" + ev.id,
                        evidence_ids=[ev.id],
                        text=ev.quote,
                        issued_at=doc.published_at,
                        horizon=None,
                        status="conflicting" if ev.quality.value == "conflict" else (
                            "applicable" if ev.quality.value == "verified" and doc.published_at
                            else "unverified"),
                    )
                )
                if doc.available_at is None:
                    result.gaps.append("bank_availability_unknown")
                if doc.published_at and doc.published_at < request.as_of:
                    result.gaps.append("bank_view_dated_not_current_confirmation")
            result.theses.sort(
                key=lambda b: (-(b.issued_at.timestamp() if b.issued_at else 0), b.id)
            )
            result.theses = result.theses[:2]
            if any(b.status == "conflicting" for b in result.theses):
                result.diagnosis = "conflicting"
                result.gaps.append("bank_source_conflict")
            elif result.theses:
                result.diagnosis = "available" if all(t.status == "applicable" for t in result.theses) else "quality_limited"
                if result.diagnosis == "quality_limited":
                    result.gaps.append("bank_ocr_unverified")
            else:
                result.gaps.append("bank_retrieval_no_typed_relevant_thesis")
            result.gaps = list(dict.fromkeys(result.gaps))
        return result
