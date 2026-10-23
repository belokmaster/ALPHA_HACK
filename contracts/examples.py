"""Deterministic handoff examples, validated against the runtime DTOs."""

from datetime import date, datetime

from contracts.v1 import (
    DTO,
    Analysis,
    AnalysisRequest,
    BankContext,
    Claim,
    ContextPatch,
    DeliveryInfo,
    Evidence,
    FeedRelevance,
    FollowupRequest,
    Grant,
    Job,
    LLMRequest,
    NewsSourceState,
    Notice,
    Period,
    Quality,
    RelevanceReason,
    Role,
    Sections,
    SessionRequest,
    TemporalAssessment,
    TemporalMode,
    UserContext,
)


def examples() -> list[DTO]:
    stamp = datetime.fromisoformat("2026-10-21T00:00:00Z")
    ctx = UserContext(
        id="alice_context",
        principal_id="alice",
        company_id="demo_a",
        version=1,
        acl_version=1,
        grants=[
            Grant(company_id="demo_a", scopes=["analysis.run", "profile.read", "sources.read"])
        ],
    )
    ev = Evidence(
        id="demo_a_evidence",
        document_version_id="demo_a_doc",
        source_version_id="synthetic_v1",
        quote="Учебное событие. Не реальная новость.",
        span="full",
        value_kind="fact",
        quality=Quality.verified,
        domain="synthetic",
        available_at=stamp,
    )
    result = Analysis(
        id="example",
        job_id="example",
        run_id="example_run",
        company_id="demo_a",
        company_snapshot_id="demo_a_v1",
        event_id="rate_hold",
        source_version_ids=["synthetic_v1"],
        context=ctx,
        status="partial",
        domain="synthetic",
        delivery=DeliveryInfo(mode="fixture", generated_at=stamp),
        temporal=TemporalAssessment(
            temporal_mode=TemporalMode.snapshot_demo,
            as_of=stamp,
            temporal_status="snapshot",
            warnings=["snapshot_not_historical_backtest"],
        ),
        sections=Sections(
            happened=["fact"],
            relevance=[],
            consequences=[],
            actions=[],
            bank_view=[],
            basis=["fact"],
        ),
        claims=[
            Claim(
                id="fact",
                kind="fact",
                origin="synthetic",
                text=ev.quote,
                evidence_ids=[ev.id],
                check="supported",
            )
        ],
        evidence=[ev],
        features=[],
        notices=[
            Notice(
                id="investment",
                kind="investment_disclaimer",
                text="Не является индивидуальной инвестиционной рекомендацией",
            ),
            Notice(
                id="synthetic",
                kind="synthetic_data",
                text="Синтетический пример; LLM не вызывалась.",
            ),
            Notice(id="ai", kind="ai_attribution", text="Не является официальной позицией банка."),
            Notice(
                id="time",
                kind="temporal_limitation",
                text="Учебный снимок; не исторический backtest.",
            ),
        ],
        missing_fields=["bank_evidence"],
        questions=["Какие условия договора?"],
        bank_evidence_status="not_found",
    )
    return [
        BankContext(diagnosis="quality_limited", gaps=["bank_ocr_unverified"]),
        FeedRelevance(
            policy_version="company-channels-1",
            channel="rates",
            score=80,
            reasons=[
                RelevanceReason(
                    code="observed_credit_product",
                    text="В профиле есть кредитные продукты",
                    weight=80,
                    feature_ids=["observed_loan"],
                    caveat="Параметры договора неизвестны",
                )
            ],
            order_rule="Сначала сумма весов, затем дата события и заголовок",
        ),
        DeliveryInfo(mode="controlled", generated_at=stamp),
        Evidence(
            id="unclassified_ocr_example",
            document_version_id="example_document_version",
            source_version_id="example_source_version",
            quote="Учебный непроверенный OCR: смесь фактов и прогнозов, не тезис банка.",
            page=1,
            span="page_chars:0:68",
            value_kind="unknown",
            quality=Quality.unverified,
            domain="synthetic",
            available_at=None,
        ),
        NewsSourceState(id="cbr-press", url="https://www.cbr.ru/rss/RssPress"),
        LLMRequest(
            run_id="engine_run",
            call_id="model_call",
            model_id="deepseek/deepseek-v4.1-flash",
            prompt="Synthetic schema example",
        ),
        SessionRequest(actor="alice"),
        ctx,
        ContextPatch(expected_version=1, role=Role.cfo),
        AnalysisRequest(event_id="rate_hold", context_version=1, as_of=stamp),
        Job(
            id="example",
            kind="analysis",
            status="queued",
            attempt=0,
            poll_url="/api/v1/jobs/example",
        ),
        result,
        FollowupRequest(analysis_id="example", context_version=1, text="Какие условия?"),
        ev,
        Period(start=date(2026, 1, 1), end=None),
        Claim(
            id="external_fact_example",
            kind="fact",
            origin="external_source",
            text="Пример внешнего факта; источник разрешается через evidence/document publisher.",
            evidence_ids=["external_example"],
            check="supported",
        ),
        Claim(
            id="model_example",
            kind="ai_inference",
            origin="model",
            text="Пример вывода модели, не позиция банка.",
            check="conditional",
            conditions=["Применим при подтверждении исходных условий"],
        ),
        Claim(
            id="hypothesis_example",
            kind="hypothesis",
            origin="model",
            text="Пример гипотезы; не подтверждённый факт.",
            check="conditional",
            conditions=["Нужна проверка договора"],
        ),
    ]
