from datetime import UTC, datetime

from sqlalchemy import select

from app.agent.limits import RunBudget
from app.auth.service import authorize, current_context, owned
from app.core import tables as t
from app.core.errors import AppError
from app.ingestion.csv.normalization import PAYMENT_PERIOD, assess_time
from contracts.v1 import (
    Analysis,
    AnalysisRequest,
    Claim,
    DeliveryInfo,
    ErrorCode,
    Event,
    Evidence,
    Notice,
    Sections,
    UserAssumption,
    UserContext,
)


async def run_fixture(c, job):
    budget = RunBudget()
    budget.transition()
    payload = job["payload"]
    ctx = UserContext.model_validate(payload["context"])
    live = await current_context(c, ctx.principal_id)
    if live.acl_version != ctx.acl_version:
        raise AppError(ErrorCode.forbidden, "Pinned grants changed", 403)
    budget.tool()
    profile = await authorize(c, live, ctx.company_id, "analysis.run")
    if job["kind"] == "followup":
        parent = Analysis.model_validate(
            (await owned(c, t.analyses, payload["parent_id"], live))["payload"]
        )
        result = parent.model_copy(deep=True)
        result.id = job["id"]
        result.job_id = job["id"]
        result.run_id = job["id"] + "_run"
        result.parent_analysis_id = parent.id
        result.conversation_id = payload["conversation_id"]
        result.assumptions = [UserAssumption.model_validate(a) for a in payload["assumptions"]]
        result.delivery = DeliveryInfo(mode="fixture", generated_at=datetime.now(UTC))
        result.status = "partial"
        question = "fixture_only: произвольный вопрос не анализировался. Доступен только учебный сценарий fixed_rate."
        if payload["request"]["scenario_id"] == "fixed_rate":
            question = "Учебный сценарий: если ставка зафиксирована, проверьте срок фиксации и условия договора. Пользовательское условие не является фактом банка."
        result.questions = [question]
        return Analysis.model_validate(result.model_dump()), budget
    req = AnalysisRequest.model_validate(payload["request"])
    if profile.snapshot_id != payload["company_snapshot_id"]:
        raise AppError(ErrorCode.data_conflict, "Pinned snapshot unavailable", 409)
    budget.tool()
    event = Event.model_validate(
        (
            await c.execute(select(t.events.c.payload).where(t.events.c.id == req.event_id))
        ).scalar_one()
    )
    # News never travels backwards in time, even in snapshot_demo.
    event_time = assess_time(
        "strict_as_of", req.as_of, event.available_at, None, [event.source_version_id]
    )
    budget.tool()
    evidence = Evidence.model_validate(
        (
            await c.execute(
                select(t.evidence.c.payload).where(t.evidence.c.id == ctx.company_id + "_evidence")
            )
        ).scalar_one()
    )
    await authorize(c, live, ctx.company_id, "sources.read")
    if (
        event.domain != "synthetic"
        or evidence.domain != "synthetic"
        or profile.source_version_id != "synthetic_v1"
    ):
        raise AppError(
            ErrorCode.data_conflict, "Fixture runner only accepts synthetic sources", 409
        )
    temporal = assess_time(
        req.temporal_mode, req.as_of, None, PAYMENT_PERIOD, [profile.source_version_id]
    )
    features = []
    for f in profile.features:
        assessment = assess_time(
            req.temporal_mode, req.as_of, f.available_at, f.period, f.depends_on_source_versions
        )
        if assessment.temporal_status != "excluded":
            features.append(f)
    claims = []
    evs = []
    if event_time.temporal_status == "included":
        evs = [evidence]
        claims.append(
            Claim(
                id="happened",
                kind="fact",
                origin="synthetic",
                text=evidence.quote,
                evidence_ids=[evidence.id],
                check="supported",
            )
        )
    if features and claims:
        claims.append(
            Claim(
                id="relevance",
                kind="ai_inference",
                origin="synthetic",
                text="Учебная связь с профилем компании; размер эффекта неизвестен.",
                feature_ids=[f.id for f in features],
                check="conditional",
                conditions=["Проверить условия договоров"],
            )
        )
    if claims:
        focus = (
            "ликвидности и условиях финансирования"
            if ctx.role.value == "cfo"
            else "операционном плане и сценариях роста"
        )
        claims.append(
            Claim(
                id="actions",
                kind="ai_inference",
                origin="synthetic",
                text="Учебный вопрос о " + focus + ".",
                check="conditional",
                conditions=["Не является рекомендацией банковского продукта"],
                alternatives=["Не менять план до получения условий договора"],
            )
        )
    ids = {cl.id for cl in claims}
    notices = [
        Notice(
            id="investment",
            kind="investment_disclaimer",
            text="Не является индивидуальной инвестиционной рекомендацией",
        ),
        Notice(
            id="ai",
            kind="ai_attribution",
            text="AI-выводы не являются официальной позицией банка. Здесь LLM не вызывалась.",
        ),
        Notice(
            id="synthetic",
            kind="synthetic_data",
            text="Синтетические данные. Fixture runner, не реальная аналитика банка.",
        ),
        Notice(
            id="time",
            kind="temporal_limitation",
            text="Платежи с 1 января 2026; окончание агрегата и дата доступности снимка неизвестны. "
            + ", ".join(temporal.warnings + event_time.warnings),
        ),
    ]
    budget.transition()
    result = Analysis(
        id=job["id"],
        job_id=job["id"],
        run_id=job["id"] + "_run",
        company_id=ctx.company_id,
        company_snapshot_id=profile.snapshot_id,
        event_id=event.id,
        source_version_ids=[profile.source_version_id],
        context=ctx,
        status="partial" if claims else "insufficient_evidence",
        domain="synthetic",
        delivery=DeliveryInfo(mode="fixture", generated_at=datetime.now(UTC)),
        temporal=temporal,
        sections=Sections(
            happened=["happened"] if "happened" in ids else [],
            relevance=["relevance"] if "relevance" in ids else [],
            consequences=[],
            actions=["actions"] if "actions" in ids else [],
            bank_view=[],
            basis=[cl.id for cl in claims],
        ),
        claims=claims,
        evidence=evs,
        features=features,
        notices=notices,
        missing_fields=["contract_terms", "bank_evidence", "snapshot_available_at"],
        questions=["Какие условия договора применяются?"],
        bank_evidence_status="not_found",
    )
    return result, budget
