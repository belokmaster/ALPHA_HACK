"""Engine behavior under controlled outputs; not a measurement of financial quality."""

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent.engine import BoundedAnalysisEngine, candidates
from app.agent.models import Draft, Proposal, Verification
from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import create_gateway
from contracts.examples import examples
from contracts.v1 import (
    AnalysisRequest,
    CompanyProfile,
    Event,
    Evidence,
    Feature,
    LLMResult,
    ModelUsage,
    SourceRef,
    UserAssumption,
    UserContext,
)


@pytest.fixture
def input_data():
    ctx = next(x for x in examples() if isinstance(x, UserContext))
    ref = SourceRef(source_version_id="case", original_filename="input.csv", sha256="a" * 64, row=2)
    feature = Feature(
        id="feature_loan",
        name="CL_PRODUCT_LOA_MARK",
        value=True,
        raw_value="1.0",
        quality="verified",
        provenance=[ref],
        depends_on_source_versions=["case"],
    )
    profile = CompanyProfile(
        company_id=ctx.company_id,
        snapshot_id="snapshot",
        source_version_id="case",
        name="Company",
        industry=None,
        client_type="CLIENT",
        status="valid",
        features=[feature],
    )
    event = Event(
        id="case_event",
        domain="case",
        title="Ключевая ставка",
        source_version_id="bank",
        available_at=None,
        event_at="2026-09-11T00:00:00Z",
        evidence_ids=["event_ev"],
        topics=["rates"],
    )
    ev = Evidence(
        id="event_ev",
        domain="case",
        document_version_id="doc",
        source_version_id="bank",
        quote="Ставка оставлена без изменения.",
        value_kind="fact",
        quality="unverified",
        span="chars:0:30",
        page=1,
        available_at=None,
    )
    return ctx, profile, event, ev


class Providers:
    def __init__(self, data):
        self.ctx, self.profile, self.event, self.ev = data

    async def get_profile(self, *args):
        return self.profile

    async def get_event(self, *args):
        return self.event

    async def get_evidence(self, ctx, ids):
        return [self.ev] if ids else []

    async def search(self, *args):
        return []

    async def origins(self, context, ids):
        return {eid: "external_source" for eid in ids}


class Scripted:
    def __init__(self, feature_id, verdict="conditional"):
        self.calls = []
        self.feature_id, self.verdict = feature_id, verdict

    async def start_run(self, *args, **kw):
        pass

    async def complete(self, req, schema):
        self.calls.append(req.purpose)
        if schema is Draft:
            value = Draft(
                proposals=[
                    Proposal(
                        text="Если потребуется новое финансирование, проверьте бюджет.",
                        section="relevance",
                        evidence_ids=["event_ev"],
                        feature_ids=[self.feature_id],
                        conditions=["Подтвердить договоры"],
                        alternatives=["Влияние может быть ограничено"],
                    )
                ]
            )
        else:
            assert schema is Verification
            value = Verification(
                verdicts=[
                    {"index": 0, "status": self.verdict, "reason": "Controlled semantic check"}
                ]
            )
        return LLMResult(
            call_id=req.call_id,
            value=value,
            usage=ModelUsage(
                model_id=req.model_id,
                usage_status="provider",
                input_tokens=100,
                output_tokens=100,
                cost_rub="0.001",
            ),
        )


@pytest.mark.parametrize("value_kind", ["forecast", "opinion"])
async def test_source_outlook_is_never_reclassified_as_happened_fact(input_data, value_kind):
    ctx, profile, event, ev = input_data
    ev.value_kind = value_kind
    tools = Providers(input_data)
    engine = BoundedAnalysisEngine(
        profiles=tools,
        events=tools,
        fragments=tools,
        retriever=tools,
        llm=Scripted(profile.features[0].id),
        job_id="job",
        mode="controlled",
    )
    result = await engine.analyze(
        ctx,
        AnalysisRequest(
            event_id=event.id, context_version=ctx.version, as_of="2026-09-14T00:00:00Z"
        ),
    )
    assert result.claims[0].kind == "hypothesis"
    assert result.claims[0].conditions and result.evidence[0].quote == ev.quote
    assert not any(c.kind == "fact" for c in result.claims)


async def test_conflicting_original_is_retained_but_never_sent_to_generation(input_data):
    ctx, profile, event, ev = input_data
    ev.quality = type(ev.quality)("conflict")
    tools, llm = Providers(input_data), Scripted(profile.features[0].id)
    engine = BoundedAnalysisEngine(
        profiles=tools,
        events=tools,
        fragments=tools,
        retriever=tools,
        llm=llm,
        job_id="job",
        mode="controlled",
    )
    result = await engine.analyze(
        ctx,
        AnalysisRequest(
            event_id=event.id, context_version=ctx.version, as_of="2026-09-14T00:00:00Z"
        ),
    )
    assert result.evidence == [ev] and not result.claims and not llm.calls
    assert result.status == "insufficient_evidence" and "source_conflict" in result.missing_fields


def test_bank_background_cannot_replace_event_premise(input_data):
    p = Proposal(
        text="Проверить условия",
        section="actions",
        evidence_ids=["bank_background"],
        feature_ids=["feature_loan"],
        conditions=["Проверить договор"],
        alternatives=["Отсутствие эффекта"],
    )
    assert not candidates(
        [p],
        {"event_ev", "bank_background"},
        input_data[1].features,
        [],
        required_event_ids={"event_ev"},
    )


@pytest.mark.parametrize("verdict", ["conditional", "unsupported", "conflicting"])
async def test_bounded_draft_verifier_removes_unsupported_and_preserves_source_quality(
    input_data, verdict
):
    ctx, profile, event, ev = input_data
    tools = Providers(input_data)
    client = Scripted(profile.features[0].id, verdict)
    engine = BoundedAnalysisEngine(
        profiles=tools,
        events=tools,
        fragments=tools,
        retriever=tools,
        llm=client,
        job_id="job",
        mode="controlled",
        assumptions=[UserAssumption(name="rate", value="фиксирована до конца 2027")],
    )
    result = await engine.analyze(
        ctx,
        AnalysisRequest(
            event_id=event.id, context_version=ctx.version, as_of="2026-09-14T00:00:00Z"
        ),
    )
    assert client.calls == ["draft", "verify"]
    assert len([c for c in result.claims if c.origin == "model"]) == (
        1 if verdict == "conditional" else 0
    )
    assert result.bank_evidence_status == "not_found" and not result.sections.bank_view
    assert result.evidence[0].quality == ev.quality == "unverified"
    assert result.claims[0].check == "conditional"
    assert result.assumptions[0].source == "user_declared"
    assert result.usage_status == "provider" and engine.budget.models_used == 2
    assert engine.budget.tools_used <= 6 and engine.budget.transitions_used <= 12


@pytest.mark.parametrize("strict,missing", [(True, False), (False, True)])
async def test_unknown_availability_or_missing_exposure_stops_generation(
    input_data, strict, missing
):
    ctx, profile, event, _ = input_data
    if missing:
        profile.features = []
    tools = Providers(input_data)
    client = Scripted("no_feature")
    engine = BoundedAnalysisEngine(
        profiles=tools,
        events=tools,
        fragments=tools,
        retriever=tools,
        llm=client,
        job_id="job",
        mode="controlled",
    )
    out = await engine.analyze(
        ctx,
        AnalysisRequest(
            event_id=event.id,
            context_version=ctx.version,
            as_of="2026-09-14T00:00:00Z",
            temporal_mode="strict_as_of" if strict else "snapshot_demo",
        ),
    )
    assert not client.calls and out.usage_status == "not_called"
    assert not [c for c in out.claims if c.origin == "model"]
    assert out.status == "insufficient_evidence"


@pytest.mark.parametrize(
    "text,evs,fids",
    [
        ("Платёж вырастет на 14%", ["event_ev"], ["feature_loan"]),
        ("По мнению банка эффект положительный", ["event_ev"], ["feature_loan"]),
        ("Если нужны средства, проверьте бюджет", ["foreign_ev"], ["feature_loan"]),
        ("Если нужны средства, проверьте бюджет", ["event_ev"], ["foreign_feature"]),
    ],
)
def test_code_checks_reject_numeric_effect_false_bank_and_foreign_refs(input_data, text, evs, fids):
    proposal = Proposal(
        text=text,
        section="relevance",
        evidence_ids=evs,
        feature_ids=fids,
        conditions=["Проверить договор"],
        alternatives=["Не менять план"],
    )
    assert not candidates([proposal], {"event_ev"}, input_data[1].features, [])


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "postgresql+asyncpg://x:y@db/working"},
        {"demo_mode": False},
        {"live_llm_enabled": True},
        {"llm_api_key": SecretStr("real-key")},
    ],
)
def test_controlled_factory_cannot_use_working_database_or_key(overrides):
    cfg = Settings(
        database_url="postgresql+asyncpg://x:y@db/alfa_test",
        demo_mode=True,
        analysis_mode="controlled",
    ).model_copy(update=overrides)
    engine = create_async_engine(cfg.database_url)
    with pytest.raises(AppError):
        create_gateway(engine, cfg)
