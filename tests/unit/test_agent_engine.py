"""Independent M04 regressions, not acceptance of the PR's hardcoded answers."""

from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_analysis_engine import Providers, Scripted
from test_analysis_engine import input_data as baseline_data

from app.agent.engine import DefaultAnalysisEngine
from app.agent.schemas import AnalysisPlan
from app.core.errors import AppError
from contracts.v1 import AnalysisRequest, ErrorCode, LLMResult, ModelUsage


@pytest.fixture
def input_data():
    return baseline_data.__wrapped__()


@pytest.mark.parametrize(
    "extra",
    [
        {"company_id": "foreign"},
        {"needs_relations": True},
        {"refined_bank_query": "x" * 501},
        {"channels": ["rates", "fuel_costs"]},
    ],
)
def test_planner_schema_cannot_expand_scope_or_unbounded_tools(extra):
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate({"channels": ["rates"], "bank_query": "query", **extra})


class Planning(Scripted):
    def __init__(self, *, plan=None, verdict="conditional", fail=None):
        super().__init__("feature_loan", verdict)
        self.plan = plan or AnalysisPlan(
            channels=["rates"],
            bank_query="initial",
            refined_bank_query="newer",
            required_fields=["contract_rate_type"],
        )
        self.fail = fail

    async def complete(self, req, schema):
        assert req.call_id == "job_" + req.purpose
        if req.purpose == self.fail:
            self.calls.append(req.purpose)
            raise AppError(ErrorCode.llm_outcome_unknown, "Controlled uncertain", 502)
        if schema is AnalysisPlan:
            self.calls.append(req.purpose)
            return LLMResult(
                call_id=req.call_id,
                value=self.plan,
                usage=ModelUsage(
                    model_id=req.model_id,
                    usage_status="provider",
                    input_tokens=100,
                    output_tokens=100,
                    cost_rub="0.001",
                ),
            )
        return await super().complete(req, schema)


class Tools(Providers):
    def __init__(self, data, pages=()):
        super().__init__(data)
        self.pages = list(pages)
        self.queries = []
        self.saved = {self.ev.id: self.ev, **{e.id: e for batch in pages for e in batch}}

    async def get_evidence(self, ctx, ids):
        return [self.saved[eid] for eid in ids]

    async def search(self, ctx, req, query):
        self.queries.append(query)
        return self.pages.pop(0) if self.pages else []


def setup(data, *, llm=None, tools=None, **kwargs):
    ctx, _, event, _ = data
    tools, llm = tools or Tools(data), llm or Planning()
    engine = DefaultAnalysisEngine(
        profiles=tools,
        events=tools,
        fragments=tools,
        retriever=tools,
        llm=llm,
        job_id="job",
        mode="controlled",
        **kwargs,
    )
    req = AnalysisRequest(
        event_id=event.id, context_version=ctx.version, as_of="2026-09-14T00:00:00Z"
    )
    return engine, llm, tools, ctx, req


async def test_adaptive_empty_search_really_executes_refinement_and_persists_new_evidence(
    input_data,
):
    bank = input_data[3].model_copy(update={"id": "new_bank", "value_kind": "forecast"})
    tools = Tools(input_data, [[], [bank]])
    engine, llm, tools, ctx, req = setup(input_data, tools=tools)
    result = await engine.analyze(ctx, req)
    assert llm.calls == ["plan", "draft", "verify"]
    assert tools.queries == ["initial", "newer"]
    assert "new_bank" in {e.id for e in result.evidence}
    assert "refined_search:empty" in engine.checkpoints
    assert engine.budget.tools_used <= 6 and engine.budget.transitions_used <= 12
    assert not result.sections.bank_view and result.bank_evidence_status == "background_only"
    assert result.job_id == "job" and result.run_id == "job_run"
    assert result.delivery.mode == "controlled"


async def test_verified_candidate_stops_search_but_never_promotes_it_to_bank_view(input_data):
    bank = input_data[3].model_copy(
        update={
            "id": "candidate",
            "value_kind": "forecast",
            "quality": type(input_data[3].quality)("verified"),
        }
    )
    engine, _, tools, ctx, req = setup(input_data, tools=Tools(input_data, [[bank]]))
    result = await engine.analyze(ctx, req)
    assert tools.queries == ["initial"] and not result.sections.bank_view


@pytest.mark.parametrize("verdict", ["unsupported", "conflicting"])
async def test_negative_semantic_verdict_is_removed_without_any_repair(input_data, verdict):
    engine, llm, _, ctx, req = setup(input_data, llm=Planning(verdict=verdict))
    result = await engine.analyze(ctx, req)
    assert not any(c.origin == "model" for c in result.claims)
    assert llm.calls == ["plan", "draft", "verify"]


@pytest.mark.parametrize("bad", ["channel", "field"])
async def test_planner_cannot_change_channel_or_request_foreign_data(input_data, bad):
    plan = AnalysisPlan(
        channels=["fuel_costs" if bad == "channel" else "rates"],
        bank_query="query",
        required_fields=["fuel_cost_share"] if bad == "field" else [],
    )
    engine, llm, _, ctx, req = setup(input_data, llm=Planning(plan=plan))
    result = await engine.analyze(ctx, req)
    assert llm.calls == ["plan"] and not any(c.origin == "model" for c in result.claims)
    assert any("llm_invalid_response" in n.text for n in result.notices)


@pytest.mark.parametrize("stage", ["plan", "draft", "verify"])
async def test_uncertain_at_any_stage_never_calls_next_stage_or_fallback(input_data, stage):
    engine, llm, _, ctx, req = setup(input_data, llm=Planning(fail=stage))
    result = await engine.analyze(ctx, req)
    assert llm.calls == ["plan", "draft", "verify"][: ["plan", "draft", "verify"].index(stage) + 1]
    assert result.usage_status == "unknown" and not any(c.origin == "model" for c in result.claims)


async def test_future_event_and_missing_exposure_stop_without_model(input_data):
    engine, llm, _, ctx, req = setup(input_data)
    input_data[2].event_at = req.as_of + timedelta(days=1)
    result = await engine.analyze(ctx, req)
    assert not llm.calls and result.status == "insufficient_evidence"


async def test_two_call_policy_uses_code_plan_with_no_hidden_third_call(input_data):
    engine, llm, _, ctx, req = setup(input_data, planning="code")
    await engine.analyze(ctx, req)
    assert llm.calls == ["draft", "verify"] and engine.budget.models_used == 2
    assert "code_plan_two_call_cap" in engine.checkpoints


async def test_followup_question_is_not_a_fake_fixed_rate_answer_and_sources_are_pinned(input_data):
    engine, _, tools, ctx, req = setup(input_data)
    parent = await engine.analyze(ctx, req)
    child, llm, _, _, _ = setup(
        input_data, parent=parent, tools=tools, question="Какова инфляция?", conversation_id="conv"
    )
    before = list(tools.queries)
    result = await child.analyze(ctx, req)
    assert llm.calls == ["draft", "verify"] and tools.queries == before
    assert not result.assumptions and not any(
        "фиксирована до конца 2027" in c.text for c in result.claims
    )
    assert result.evidence == parent.evidence and result.bank_context == parent.bank_context
    assert [c for c in result.claims if c.id in result.sections.happened] == [
        c for c in parent.claims if c.id in parent.sections.happened
    ]
    assert result.parent_analysis_id == parent.id and result.delivery.mode == "controlled"


async def test_followup_rejects_changed_company_before_any_model_call(input_data):
    engine, _, _, ctx, req = setup(input_data)
    parent = await engine.analyze(ctx, req)
    other = ctx.model_copy(update={"company_id": "other"})
    child, llm, _, _, _ = setup(input_data, parent=parent)
    with pytest.raises(AppError, match="Follow-up context"):
        await child.analyze(other, req)
    assert not llm.calls


@pytest.mark.parametrize('wrong_index', [False, True])
async def test_followup_filtered_indices_and_conditional_actions(input_data, wrong_index):
    from app.agent.models import Draft, Proposal, Verification
    from contracts.v1 import UserAssumption

    class Action(Scripted):
        async def complete(self, request, schema):
            result = await super().complete(request, schema)
            if schema is Draft:
                base = Proposal(
                    text="Если планируется новый кредит, запросите условия до изменения бюджета.",
                    section="actions", feature_ids=["feature_loan"], evidence_ids=["event_ev"],
                    conditions=["Только при планах нового финансирования; текущий договор фиксирован по сообщению пользователя"],
                    alternatives=["Если новых заимствований нет, подтвердите отсутствие пересмотра по договору"],
                )
                result.value = Draft(proposals=[
                    base.model_copy(update={"text": "Платежи вырастут на 7%"}), base])
            elif schema is Verification:
                result.value = Verification(verdicts=[{
                    "index": 0 if wrong_index else 1, "status": "conditional",
                    "reason": "Проверка нового финансирования, без эффекта на действующий договор",
                }])
            return result

    engine, _, tools, ctx, req = setup(input_data)
    parent = await engine.analyze(ctx, req)
    child, llm, _, _, _ = setup(
        input_data, parent=parent, tools=tools, llm=Action("feature_loan"),
        question="Что проверить при фиксированной ставке?",
        assumptions=[UserAssumption(name="contract_rate_type", value="fixed")],
    )
    result = await child.analyze(ctx, req)
    models = [c for c in result.claims if c.origin == "model"]
    assert llm.calls == ["draft", "verify"]
    assert len(models) == (0 if wrong_index else 1)
    assert not result.sections.consequences
    if models:
        assert result.sections.actions == [models[0].id]
        assert "contract_rate_type=fixed" in " ".join(models[0].conditions)
    assert result.assumptions[0].source == "user_declared"


@pytest.mark.parametrize("fixed", [False, True])
def test_declared_fixed_rate_cannot_create_model_effect_even_with_positive_verifier(fixed):
    from app.agent.guardrails import candidates, verified_proposals
    from app.agent.models import Proposal, Verdict
    from contracts.v1 import UserAssumption

    data = baseline_data.__wrapped__()
    proposal = Proposal(
        text="Сохранение ключевой ставки снижает риск изменения бюджета по фиксированному кредиту.",
        section="consequences", impact_kind="opportunity", evidence_ids=["event_ev"],
        feature_ids=["feature_loan"], conditions=["Договор фиксирован по сообщению пользователя"],
        alternatives=["Проверить договор"],
    )
    assumptions = [UserAssumption(name="contract_rate_type", value="fixed")] if fixed else []
    proposed = candidates([proposal], {"event_ev"}, data[1].features, assumptions,
                          required_event_ids={"event_ev"}, channel="rates")
    assert len(verified_proposals(proposed, [Verdict(index=0, status="conditional",
                                                  reason="Контролируемый ложный допуск")])) == (0 if fixed else 1)


async def test_material_clarification_stops_before_draft(input_data):
    llm = Planning(plan=AnalysisPlan(channels=["rates"], bank_query="query",
                                    next_action="clarify", required_fields=["contract_rate_type"]))
    engine, llm, tools, ctx, req = setup(input_data, llm=llm)
    engine.planning = "adaptive"
    result = await engine.analyze(ctx, req)
    assert llm.calls == ["plan"]
    assert not tools.queries
    assert not result.sections.consequences
    assert any("clarification_required" in n.text for n in result.notices)


@pytest.mark.parametrize("quality", ["verified", "unverified"])
async def test_exact_dated_bank_quote_is_separate_from_personal_effect(input_data, quality):
    from contracts.v1 import BankContext, BankThesis, Quality

    ev = input_data[3].model_copy(update={"id": "bank_quote", "value_kind": "forecast",
                                        "quality": Quality(quality),
                                        "quote": "Банк ожидает постепенного снижения ставки."})
    tools = Tools(input_data, pages=[[ev]])

    async def describe(*args):
        return BankContext(diagnosis="quality_limited", theses=[BankThesis(
            id="thesis", evidence_ids=[ev.id], text=ev.quote, status="applicable" if quality == "verified" else "unverified",
            issued_at="2026-09-11T00:00:00Z", horizon=None)])

    tools.describe_bank = describe
    engine, _, _, ctx, req = setup(input_data, tools=tools, bank_context_provider=tools)
    result = await engine.analyze(ctx, req)
    assert bool(result.sections.bank_view) == (quality == "verified")
    if result.sections.bank_view:
        claim = next(c for c in result.claims if c.id == result.sections.bank_view[0])
        assert claim.text == ev.quote and claim.origin == "bank_analysis"
        assert not claim.feature_ids


def test_six_distinct_verdicts_keep_indices_and_remove_rejected():
    from app.agent.guardrails import verified_proposals
    from app.agent.models import Draft, Proposal, Verification

    proposal = Proposal(text="Условный эффект", section="consequences", evidence_ids=["e"],
                        feature_ids=["f"], conditions=["Если условие выполнено"], alternatives=["Эффекта может не быть"])
    draft = Draft(proposals=[proposal.model_copy() for _ in range(6)])
    checked = Verification(verdicts=[{"index": i, "status": "unsupported" if i == 4 else "conditional", "reason": "Проверено"} for i in range(6)])
    assert [i for i, _ in verified_proposals(list(enumerate(draft.proposals)), checked.verdicts)] == [0, 1, 2, 3, 5]


@pytest.mark.parametrize("until,window,expected", [(None, "current", False), ("bad", "after_fixed_until", False),
                                                 ("2027-12-31", "current", False), ("2027-12-31", "after_fixed_until", True)])
def test_fixed_effect_requires_explicit_horizon_and_temporal_condition(input_data, until, window, expected):
    from app.agent.guardrails import candidate_rejection
    from app.agent.models import Proposal
    from contracts.v1 import UserAssumption

    assumptions = [UserAssumption(name="contract_rate_type", value="fixed")]
    if until:
        assumptions.append(UserAssumption(name="fixed_until", value=until))
    p = Proposal(text="Условия нового финансирования могут отличаться.", section="consequences",
                 effect_window=window, evidence_ids=["event_ev"], feature_ids=["feature_loan"],
                 conditions=["После окончания фиксации при новом финансировании"], alternatives=["Новый заём может не понадобиться"])
    assert (candidate_rejection(p, {"event_ev"}, input_data[1].features, channel="rates", assumptions=assumptions) is None) == expected
