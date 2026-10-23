from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import insert, select, text, update
from starlette.exceptions import HTTPException

from app import sources
from app.agent.engine import ENGINE_POLICY_VERSION
from app.auth.service import authenticate, authorize, current_context, digest, owned, profile
from app.core import tables as t
from app.core.config import settings
from app.core.db import EXPECTED_DB_REVISION, engine
from app.core.errors import AppError
from app.ingestion.news.service import recent_events, source_states, stale_reasons
from app.jobs.service import enqueue, job_dto, replay
from app.llm.pinning import live_pin
from app.llm.runtime import load_runtime
from contracts.v1 import (
    SCHEMA_VERSION,
    Analysis,
    AnalysisRequest,
    APIError,
    CompanyProfile,
    ContextPatch,
    Conversation,
    Document,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Evidence,
    Feature,
    FeedItem,
    FollowupRequest,
    Job,
    NewsSourceState,
    SessionRequest,
    SessionResponse,
    SourceVersion,
    UserContext,
)

app = FastAPI(
    title="Alfa synthetic backend foundation",
    version=SCHEMA_VERSION,
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "web/templates"), autoescape=select_autoescape()
)


@app.middleware("http")
async def request_policy(request: Request, call_next):
    request.state.request_id = str(uuid4())
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") not in (
        None,
        settings.app_origin,
    ):
        return error_response(request, ErrorCode.forbidden, "Origin denied", 403)
    try:
        response = await call_next(request)
    except Exception:
        return error_response(request, ErrorCode.internal, "Internal error", 500)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def error_response(request, code, message, status):
    dto = ErrorEnvelope(
        error=APIError(code=code, message=message, request_id=request.state.request_id)
    )
    return JSONResponse(dto.model_dump(mode="json"), status_code=status)


@app.exception_handler(AppError)
async def domain_error(request: Request, exc: AppError):
    return error_response(request, exc.code, exc.message, exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_response(request, ErrorCode.validation, "Invalid request", 422)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(request, ErrorCode.not_found, "Resource not found", exc.status_code)


@app.get("/health/live")
async def live():
    return {"status": "live"}


@app.get("/health/ready")
async def ready():
    try:
        async with engine.connect() as c:
            revision = (
                await c.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        if revision != EXPECTED_DB_REVISION or len(settings.app_secret.get_secret_value()) < 32:
            raise ValueError("configuration")
    except Exception:
        raise AppError(
            ErrorCode.backend_unavailable, "Database/migration/configuration not ready", 503
        ) from None
    return {
        "status": "ready",
        "mode": settings.analysis_mode,
        "live_llm": settings.live_llm_enabled,
        "code_sha": settings.app_code_sha,
        "runtime_sha256": load_runtime(settings).sha256,
        "session_id": settings.llm_live_session_id or None,
        "schema_revision": revision,
    }


@app.get("/", response_class=HTMLResponse)
async def shell():
    return env.get_template("index.html").render(demo=settings.demo_mode)


@app.post("/api/v1/demo/sessions", response_model=SessionResponse)
async def login(body: SessionRequest, response: Response):
    if not settings.demo_mode:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    if len(settings.app_secret.get_secret_value()) < 32:
        raise AppError(
            ErrorCode.backend_unavailable, "APP_SECRET must contain at least 32 characters", 503
        )
    token, csrf = token_urlsafe(32), token_urlsafe(32)
    expiry = datetime.now(UTC) + timedelta(hours=settings.session_hours)
    async with engine.begin() as c:
        ctx = await current_context(c, body.actor)
        await c.execute(
            insert(t.sessions).values(
                id=digest(token), principal_id=body.actor, csrf_hash=digest(csrf), expires_at=expiry
            )
        )
    response.set_cookie(
        "alfa_session",
        token,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        max_age=settings.session_hours * 3600,
    )
    return SessionResponse(context=ctx, csrf_token=csrf, expires_at=expiry)


@app.get("/api/v1/me/context", response_model=UserContext)
async def context(request: Request):
    async with engine.connect() as c:
        return await authenticate(c, request)


@app.patch("/api/v1/me/context", response_model=UserContext)
async def patch_context(body: ContextPatch, request: Request):
    async with engine.begin() as c:
        old = await authenticate(c, request)
        ctx = await current_context(c, old.principal_id, lock=True)
        if ctx.version != body.expected_version:
            raise AppError(ErrorCode.context_stale, "Context version changed", 409)
        if body.company_id is not None:
            await authorize(c, ctx, body.company_id, "profile.read")
            ctx.company_id = body.company_id
        if body.role is not None:
            ctx.role = body.role
            ctx.role_source = "user_selected"
        if body.preferences is not None:
            ctx.preferences = body.preferences
        ctx.version += 1
        await c.execute(
            update(t.contexts)
            .where(t.contexts.c.id == ctx.id)
            .values(version=ctx.version, payload=ctx.model_dump(mode="json"))
        )
        return ctx


@app.get("/api/v1/companies", response_model=list[CompanyProfile])
async def companies(request: Request):
    async with engine.connect() as c:
        ctx = await authenticate(c, request)
        return [
            await profile(c, ctx, g.company_id) for g in ctx.grants if "profile.read" in g.scopes
        ]


@app.get("/api/v1/companies/{company_id}/profile", response_model=CompanyProfile)
async def get_profile(company_id: str, request: Request):
    async with engine.connect() as c:
        return await profile(c, await authenticate(c, request), company_id)


@app.get("/api/v1/feed", response_model=list[FeedItem])
async def feed(
    request: Request, as_of: datetime | None = None, temporal_mode: str = "snapshot_demo"
):
    from app.personalization import rank_events
    from contracts.v1 import AnalysisRequest

    cutoff = as_of or datetime.now(UTC)
    if cutoff.tzinfo is None or temporal_mode not in ("snapshot_demo", "strict_as_of"):
        raise AppError(ErrorCode.validation, "Timezone and valid temporal mode required", 422)
    async with engine.connect() as c:
        ctx = await authenticate(c, request)
        await authorize(c, ctx, ctx.company_id, "news.read")
        # Ranking discloses company features, so news access alone is insufficient.
        p = await profile(c, ctx, ctx.company_id)
        domain = (
            await c.scalar(
                select(t.source_versions.c.payload).where(
                    t.source_versions.c.id == p.source_version_id
                )
            )
        )["domain"]
        raw = (
            await c.execute(
                select(t.events.c.payload).where(
                    t.events.c.payload["domain"].astext.in_(
                        ["synthetic"] if domain == "synthetic" else ["case"]
                    )
                )
            )
        ).scalars()
        events = [Event.model_validate(v) for v in raw]
        if domain != "synthetic":
            events += [Event.model_validate(v) for v in await recent_events(c, 50, None)]
        visible = []
        for event in events:
            if event.domain != "synthetic" and not event.evidence_ids:
                continue
            try:
                for eid in event.evidence_ids:
                    await sources.evidence(c, ctx, eid)
                visible.append(event)
            except AppError as exc:
                if exc.code != ErrorCode.not_found:
                    raise
        req = AnalysisRequest(
            event_id="feed", context_version=ctx.version, as_of=cutoff, temporal_mode=temporal_mode
        )
        return rank_events(p, visible, ctx, req)


@app.get("/api/v1/news", response_model=list[Event])
async def news(request: Request, limit: int = 20, source: str | None = None):
    if not 1 <= limit <= 50 or source not in (None, "cbr", "rosstat"):
        raise AppError(ErrorCode.validation, "Invalid news filter", 422)
    async with engine.connect() as c:
        ctx = await authenticate(c, request)
        await authorize(c, ctx, ctx.company_id, "news.read")
        return await recent_events(c, limit, source)


@app.get("/api/v1/news/sources", response_model=list[NewsSourceState])
async def news_sources(request: Request):
    async with engine.connect() as c:
        ctx = await authenticate(c, request)
        await authorize(c, ctx, ctx.company_id, "news.read")
        return await source_states(c)


@app.post("/api/v1/analyses", response_model=Job, status_code=202)
async def analysis(body: AnalysisRequest, request: Request):
    async with engine.begin() as c:
        ctx = await authenticate(c, request)
        ctx = await current_context(c, ctx.principal_id, lock=True)
        route = "/api/v1/analyses"
        key = request.headers.get("Idempotency-Key")
        raw = body.model_dump(mode="json")
        old, h = await replay(c, ctx, route, key, raw)
        if old:
            await authorize(c, ctx, old["company_id"], "analysis.run")
            return job_dto(old)
        if body.context_version != ctx.version:
            raise AppError(ErrorCode.context_stale, "Context version changed", 409)
        p = await authorize(c, ctx, ctx.company_id, "analysis.run")
        event_raw = (
            await c.execute(select(t.events.c.payload).where(t.events.c.id == body.event_id))
        ).scalar_one_or_none()
        if event_raw is None:
            raise AppError(ErrorCode.not_found, "Resource not found", 404)
        event = Event.model_validate(event_raw)
        profile_source = (
            await c.execute(
                select(t.source_versions.c.payload).where(
                    t.source_versions.c.id == p.source_version_id
                )
            )
        ).scalar_one()
        if (event.domain == "synthetic") != (profile_source["domain"] == "synthetic"):
            raise AppError(
                ErrorCode.data_conflict, "Choose matching real or synthetic profile/event", 409
            )
        for eid in event.evidence_ids:
            await sources.evidence(c, ctx, eid)
        payload = {
            "request": raw,
            "context": ctx.model_dump(mode="json"),
            "company_snapshot_id": p.snapshot_id,
            "event_version": event.id,
            "runner": "fixture_runner" if event.domain == "synthetic" else "analysis_engine",
            "source_versions": [p.source_version_id],
            "policy_version": "foundation-1"
            if event.domain == "synthetic"
            else ENGINE_POLICY_VERSION,
        }
        if event.domain != "synthetic":
            runtime = load_runtime(settings)
            payload["llm_runtime"] = runtime.snapshot("adaptive")
            payload["llm_live_pin"] = live_pin(settings, runtime)
        return await enqueue(c, ctx, route, key, raw, payload, "analysis", h)


@app.get("/api/v1/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, request: Request):
    async with engine.connect() as c:
        return job_dto(await owned(c, t.jobs, job_id, await authenticate(c, request)))


@app.get("/api/v1/analyses/{analysis_id}", response_model=Analysis)
async def get_analysis(analysis_id: str, request: Request):
    async with engine.connect() as c:
        row = await owned(c, t.analyses, analysis_id, await authenticate(c, request))
        result = Analysis.model_validate(row["payload"])
        current = await current_context(c, result.context.principal_id)
        if current.acl_version != result.context.acl_version:
            raise AppError(ErrorCode.not_found, "Resource not found", 404)
        reasons = await stale_reasons(c, result)
        if reasons:
            result.delivery.stale = True
            result.delivery.stale_reasons = list(
                dict.fromkeys(result.delivery.stale_reasons + reasons)
            )
        result.delivery.original_mode = result.delivery.mode
        result.delivery.mode = "saved_replay"
        return result


@app.get("/api/v1/evidence/{evidence_id}", response_model=Evidence)
async def get_evidence(evidence_id: str, request: Request):
    async with engine.connect() as c:
        return await sources.evidence(c, await authenticate(c, request), evidence_id)


@app.get("/api/v1/sources/{document_id}", response_model=Document)
async def get_source(document_id: str, request: Request):
    async with engine.connect() as c:
        return await sources.document(c, await authenticate(c, request), document_id)


@app.get("/api/v1/sources/{document_id}/provenance", response_model=SourceVersion)
async def source_provenance(document_id: str, request: Request):
    async with engine.connect() as c:
        doc = await sources.document(c, await authenticate(c, request), document_id)
        return (
            await c.execute(
                select(t.source_versions.c.payload).where(
                    t.source_versions.c.id == doc.source_version_id
                )
            )
        ).scalar_one()


@app.get("/api/v1/sources/{document_id}/asset", response_class=FileResponse)
async def source_asset(document_id: str, request: Request):
    import hashlib

    from app.ingestion.pdf.store import is_bank_document

    async with engine.connect() as c:
        doc = await sources.document(c, await authenticate(c, request), document_id)
        if not await is_bank_document(c, doc.id):
            raise AppError(ErrorCode.not_found, "No approved PDF asset", 404)
        sv = SourceVersion.model_validate(
            (
                await c.execute(
                    select(t.source_versions.c.payload).where(
                        t.source_versions.c.id == doc.source_version_id
                    )
                )
            ).scalar_one()
        )
    root = Path(__file__).resolve().parents[1] / "raw/pdf"
    path = (root / sv.ref.original_filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AppError(ErrorCode.backend_unavailable, "Local source asset unavailable", 503)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sv.ref.sha256:
        raise AppError(ErrorCode.data_conflict, "Source asset hash mismatch", 409)
    return FileResponse(
        path, media_type="application/pdf", content_disposition_type="inline", filename=path.name
    )


@app.get("/sources/{document_id}", response_class=HTMLResponse, include_in_schema=False)
async def source_page(document_id: str, request: Request):
    doc = await get_source(document_id, request)
    return env.get_template("source.html").render(doc=doc, evidence=None)


@app.get("/evidence/{evidence_id}", response_class=HTMLResponse, include_in_schema=False)
async def evidence_page(evidence_id: str, request: Request):
    ev = await get_evidence(evidence_id, request)
    doc = await get_source(ev.document_version_id, request)
    return env.get_template("source.html").render(doc=doc, evidence=ev)


@app.get("/api/v1/features/{feature_id}", response_model=Feature)
async def feature_source(feature_id: str, request: Request):
    async with engine.connect() as c:
        ctx = await authenticate(c, request)
        row = (
            (
                await c.execute(
                    select(t.features.c.payload, t.companies.c.company_id)
                    .join(t.companies, t.companies.c.id == t.features.c.snapshot_id)
                    .where(t.features.c.id == feature_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AppError(ErrorCode.not_found, "Resource not found", 404)
        await authorize(c, ctx, row["company_id"], "sources.read")
        return row["payload"]


@app.post("/api/v1/followups", response_model=Job, status_code=202)
async def followup(body: FollowupRequest, request: Request):
    async with engine.begin() as c:
        ctx = await authenticate(c, request)
        ctx = await current_context(c, ctx.principal_id, lock=True)
        route = "/api/v1/followups"
        key = request.headers.get("Idempotency-Key")
        raw = body.model_dump(mode="json")
        old, h = await replay(c, ctx, route, key, raw)
        if old:
            await authorize(c, ctx, old["company_id"], "analysis.run")
            return job_dto(old)
        parent = Analysis.model_validate(
            (await owned(c, t.analyses, body.analysis_id, ctx))["payload"]
        )
        if body.context_version != parent.context.version:
            raise AppError(ErrorCode.context_stale, "Use pinned analysis context version", 409)
        if parent.domain != "synthetic":
            from app.agent.providers import pinned

            await pinned(c, parent.context)
        if body.conversation_id:
            convrow = await owned(c, t.conversations, body.conversation_id, ctx)
            if convrow["analysis_id"] != body.analysis_id:
                raise AppError(ErrorCode.not_found, "Resource not found", 404)
            conv = Conversation.model_validate(convrow["payload"])
        else:
            conv = Conversation(
                id=str(uuid4()),
                analysis_id=body.analysis_id,
                principal_id=ctx.principal_id,
                assumptions=[],
            )
            await c.execute(
                insert(t.conversations).values(
                    id=conv.id,
                    analysis_id=body.analysis_id,
                    principal_id=ctx.principal_id,
                    payload=conv.model_dump(mode="json"),
                )
            )
        merged = {a.name: a for a in conv.assumptions}
        merged.update({a.name: a for a in body.assumptions})
        conv.assumptions = list(merged.values())
        await c.execute(
            update(t.conversations)
            .where(t.conversations.c.id == conv.id)
            .values(payload=conv.model_dump(mode="json"))
        )
        payload = {
            "request": raw,
            "context": parent.context.model_dump(mode="json"),
            "parent_id": parent.id,
            "conversation_id": conv.id,
            "assumptions": [a.model_dump(mode="json") for a in conv.assumptions],
            "runner": "fixture_runner" if parent.domain == "synthetic" else "analysis_engine",
            "policy_version": parent.policy_version,
        }
        if parent.domain != "synthetic":
            # Follow-up is a NEW run: inherit stage policy from config at enqueue.
            runtime = load_runtime(settings)
            payload["llm_runtime"] = runtime.snapshot("adaptive")
            payload["llm_live_pin"] = live_pin(settings, runtime)
        pinned = parent.context
        return await enqueue(c, pinned, route, key, raw, payload, "followup", h)
