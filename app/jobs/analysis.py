"""Worker-side construction: shared gateway, local retrieval, short-lived DB tools."""

from sqlalchemy import select

from app.agent.engine import ENGINE_POLICY_VERSION, DefaultAnalysisEngine
from app.agent.events import DBEventProvider
from app.agent.providers import BankSearch, Profiles
from app.auth.service import owned
from app.core import tables as t
from app.core.config import settings
from app.core.errors import AppError
from app.llm.factory import create_gateway
from app.llm.g1_live import POLICY as LIVE_G1_POLICY
from app.llm.g1_live import LiveG1Authorization, LiveG1Client
from app.llm.logging import emit
from app.llm.pinning import live_pin
from app.llm.runtime import pinned_runtime
from app.retrieval.service import HybridRetriever, LocalE5Encoder
from contracts.v1 import Analysis, AnalysisRequest, ErrorCode, UserAssumption, UserContext

_encoder = None


async def run_analysis(engine, job):
    global _encoder
    payload = job["payload"]
    live_authorization = payload.get("live_g1_authorization")
    expected_policy = LIVE_G1_POLICY if live_authorization is not None else ENGINE_POLICY_VERSION
    if payload.get("policy_version", ENGINE_POLICY_VERSION) != expected_policy:
        raise AppError(ErrorCode.data_conflict, "Pinned engine policy unavailable", 409)
    if payload.get("required_worker_mode", settings.analysis_mode) != settings.analysis_mode:
        raise AppError(
            ErrorCode.backend_unavailable, "Worker mode differs from the explicit smoke", 503
        )
    runtime, runtime_mode = pinned_runtime(payload.get("llm_runtime"))
    if settings.live_llm_enabled and settings.analysis_mode == "gateway":
        if payload.get("llm_live_pin") != live_pin(settings, runtime):
            raise AppError(ErrorCode.data_conflict, "Pinned live session/tariff/endpoint differs", 409)
    emit("analysis_start", job_id=job["id"], session_id=settings.llm_live_session_id or None,
         config_hash=runtime.sha256, code_sha=settings.app_code_sha)
    ctx = UserContext.model_validate(payload["context"])
    profiles = Profiles(engine)
    profile = await profiles.get_profile(ctx, ctx.company_id)
    async with engine.connect() as c:
        profile_domain = (
            await c.execute(
                select(t.source_versions.c.payload).where(
                    t.source_versions.c.id == profile.source_version_id
                )
            )
        ).scalar_one()["domain"]
    if profile_domain == "synthetic":
        raise AppError(
            ErrorCode.data_conflict, "Real analysis requires a non-synthetic profile", 409
        )
    parent = None
    if job["kind"] == "followup":
        async with engine.connect() as c:
            parent = Analysis.model_validate(
                (await owned(c, t.analyses, payload["parent_id"], ctx))["payload"]
            )
        req = AnalysisRequest(
            event_id=parent.event_id,
            context_version=ctx.version,
            as_of=parent.temporal.as_of,
            temporal_mode=parent.temporal.temporal_mode,
        )
        snapshot = parent.company_snapshot_id
    else:
        req = AnalysisRequest.model_validate(payload["request"])
        snapshot = payload["company_snapshot_id"]
    if profile.snapshot_id != snapshot:
        raise AppError(ErrorCode.data_conflict, "Pinned snapshot changed", 409)
    if _encoder is None:
        # Explicit local assets only; never downloads on startup or missing weights.
        _encoder = LocalE5Encoder()
    gateway = create_gateway(engine, settings, runtime=runtime, mode=runtime_mode)
    llm = gateway
    if live_authorization is not None:
        if settings.analysis_mode != "gateway":
            raise AppError(ErrorCode.forbidden, "Live G1 requires gateway worker", 403)
        llm = LiveG1Client(
            gateway, job["id"], LiveG1Authorization.model_validate(live_authorization)
        )
    bank_search = BankSearch(engine, HybridRetriever(engine, _encoder))
    controller = DefaultAnalysisEngine(
        profiles=profiles,
        events=DBEventProvider(engine),
        fragments=DBEventProvider(engine),
        retriever=bank_search,
        bank_context_provider=bank_search,
        llm=llm,
        job_id=job["id"],
        mode=settings.analysis_mode,
        planning="code" if live_authorization is not None else "adaptive",
        runtime=runtime,
        runtime_mode=runtime_mode,
        assumptions=[UserAssumption.model_validate(a) for a in payload.get("assumptions", [])],
        parent=parent,
        conversation_id=payload.get("conversation_id"),
        question=payload["request"].get("text"),
    )
    controller.checkpoints.append("code_sha:" + settings.app_code_sha)
    result = await controller.analyze(ctx, req)
    return result, controller
