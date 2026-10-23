"""M04 integration: validated planning, adaptive local retrieval, draft and verify.

Normal runs use at most three model calls, operator smoke at most two. No repair,
numeric effect, automatic bank attribution or hidden retry. OCR remains unverified.
"""

import json
from datetime import UTC, datetime

from app.agent.followup import assert_followup_context
from app.agent.guardrails import candidate_rejection, candidates, verified_proposals
from app.agent.limits import RunBudget, RunLimits
from app.agent.models import Draft, Verification
from app.agent.prompts import render_planner_prompt
from app.agent.schemas import AnalysisPlan
from app.core.errors import AppError
from app.ingestion.csv.normalization import assess_time
from app.llm.runtime import RuntimeConfig, load_runtime
from app.llm.transport import input_bound, messages_for
from app.personalization import match_event, usable_features
from contracts.ports import (
    AnalysisEngine,
    EventProvider,
    EvidenceProvider,
    EvidenceRetriever,
    LLMClient,
    ProfileProvider,
)
from contracts.v1 import (
    Analysis,
    AnalysisRequest,
    BankContext,
    Claim,
    DeliveryInfo,
    ErrorCode,
    LLMRequest,
    Notice,
    Sections,
    UserContext,
)

ENGINE_POLICY_VERSION = "m04-adaptive-3"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class BoundedAnalysisEngine(AnalysisEngine):
    def __init__(
        self,
        *,
        profiles: ProfileProvider,
        events: EventProvider,
        fragments: EvidenceProvider,
        retriever: EvidenceRetriever,
        llm: LLMClient,
        job_id: str,
        assumptions=(),
        parent=None,
        conversation_id=None,
        mode="evidence_only",
        question=None,
        bank_context_provider=None,
        planning="code",
        runtime: RuntimeConfig | None = None,
        runtime_mode="adaptive",
    ):
        self.profiles, self.events, self.retriever, self.llm = profiles, events, retriever, llm
        self.fragments, self.question = fragments, question
        self.bank_context_provider = bank_context_provider
        self.job_id, self.assumptions, self.parent = job_id, list(assumptions), parent
        self.conversation_id, self.mode = conversation_id, mode
        self.planning = planning
        self.session_id = getattr(getattr(getattr(llm, "gateway", llm), "config", None),
                                  "live_session_id", "")
        self.runtime = runtime or load_runtime()
        self.runtime_mode = runtime_mode
        caps = self.runtime.for_mode(runtime_mode)
        self.budget = RunBudget(
            RunLimits(
                transitions=self.runtime.limits.transitions,
                tools=self.runtime.limits.tools,
                repeated_searches=self.runtime.limits.repeated_searches,
                models=min(caps.max_model_calls, 3 if planning == "adaptive" else 2),
                seconds=self.runtime.timeouts.run_seconds,
                input_tokens=caps.input_tokens,
                output_tokens=caps.output_tokens,
            )
        )
        self.checkpoints = [
            "llm_runtime:" + self.runtime.version + ":" + self.runtime.sha256,
            "llm_runtime_mode:" + runtime_mode,
        ]
        self.tools = []
        if self.session_id:
            self.checkpoints.append("llm_session:" + self.session_id)

    async def fragments_at(self, context, request, ids):
        if hasattr(self.fragments, "get_evidence_at"):
            return await self.fragments.get_evidence_at(context, request, ids)
        return await self.fragments.get_evidence(context, ids)

    def tool(self, name):
        self.budget.tool()
        self.tools.append(name)

    def step(self, name):
        from app.llm.logging import emit

        emit("controller_decision", job_id=self.job_id, run_id=self.job_id + "_run",
             session_id=self.session_id or None, decision=name, config_hash=self.runtime.sha256)
        self.budget.transition()
        self.checkpoints.append(name)

    async def analyze(self, context: UserContext, request: AnalysisRequest) -> Analysis:
        run_id = self.job_id + "_run"
        self.step("scope_and_versions")
        if self.parent:
            assert_followup_context(self.parent, context)
        error = None
        usage_status, model_id = "not_called", None
        used_models = set()
        last_model_id = None
        if self.mode != "evidence_only":
            try:
                await self.llm.start_run(run_id, job_id=self.job_id)
            except AppError as exc:
                error = exc.code.value

        async def call(purpose, prompt, schema):
            nonlocal model_id, usage_status, last_model_id
            stage = self.runtime.stage(purpose)
            tokens = stage.max_output_tokens
            # Re-authorize immediately before every paid boundary. No DB
            # connection/transaction is retained across complete().
            await self.profiles.get_profile(context, context.company_id)
            bound = input_bound(messages_for(prompt, schema))
            if bound > self.runtime.limits.input_per_call:
                raise AppError(ErrorCode.budget_exceeded, "Stage input envelope exceeded", 429)
            self.budget.model(input_tokens=bound, output_tokens=tokens)
            last_model_id = stage.model_id
            self.checkpoints.append("llm_attempt:" + purpose + ":" + stage.model_id)
            response = await self.llm.complete(
                LLMRequest(
                    run_id=run_id,
                    call_id=self.job_id + "_" + purpose,
                    model_id=stage.model_id,
                    prompt=prompt,
                    purpose=purpose,
                    max_output_tokens=tokens,
                ),
                schema,
            )
            model_id, usage_status = response.usage.model_id, response.usage.usage_status
            if model_id:
                used_models.add(model_id)
            self.checkpoints.append("llm_stage:" + purpose + ":" + stage.model_id)
            return response.value

        self.tool("get_profile")
        profile = await self.profiles.get_profile(context, context.company_id)
        self.tool("get_event")
        event = await self.events.get_event(context, request.event_id)
        if event.domain == "synthetic":
            raise ValueError("Synthetic events require the separate fixture runner")
        temporal = assess_time(
            request.temporal_mode,
            request.as_of,
            event.available_at,
            None,
            [event.source_version_id, profile.source_version_id],
        )
        permitted = event.event_at <= request.as_of and temporal.temporal_status != "excluded"
        self.tool("get_evidence")
        # The concrete event adapter implements the bounded get_evidence tool as well.
        event_evidence = await self.fragments_at(context, request, event.evidence_ids[:8])
        event_evidence = [
            e
            for e in event_evidence
            if permitted
            and (e.available_at is None or e.available_at <= request.as_of)
            and assess_time(
                request.temporal_mode, request.as_of, e.available_at, None, [e.source_version_id]
            ).temporal_status
            != "excluded"
        ]
        source_conflict = any(e.quality.value == "conflict" for e in event_evidence)
        original_event_evidence = event_evidence
        event_evidence = [e for e in event_evidence if e.quality.value != "conflict"]
        features = usable_features(profile, request)
        match = match_event(profile, event, request)
        selected = match.selected
        missing = list(match.missing)
        if event.available_at is None:
            missing.append("event_available_at")
        if not event_evidence:
            missing.append("event_evidence_missing")
        if source_conflict:
            missing.append("source_conflict")
        missing.append("independent_page_review")
        relevant_channel = match.channel != "unsupported"
        self.step("time_and_material_unknowns")
        bank = []
        bank_context = BankContext(gaps=["bank_context_adapter_missing"])
        query = match.query or event.title[:350]
        refined_query = None
        planner_fields = []
        if self.parent:
            assert_followup_context(self.parent, context)
            if (
                profile.snapshot_id != self.parent.company_snapshot_id
                or request.as_of != self.parent.temporal.as_of
                or request.temporal_mode != self.parent.temporal.temporal_mode
            ):
                raise AppError(ErrorCode.data_conflict, "Follow-up versions differ", 409)
            # Re-authorize immutable parent IDs in batches; no new retrieval/version substitution.
            frozen = []
            ids = [e.id for e in self.parent.evidence]
            for offset in range(0, len(ids), 8):
                self.tool("get_evidence")
                frozen += await self.fragments_at(context, request, ids[offset : offset + 8])
            if {e.id for e in frozen} != set(ids):
                raise AppError(
                    ErrorCode.data_conflict, "Pinned evidence unavailable at cutoff", 409
                )
            bank = [e for e in frozen if e.id not in set(event.evidence_ids)]
            bank_context = self.parent.bank_context.model_copy(deep=True)
            self.checkpoints.append("followup_pinned_sources_reused")
        else:
            if (
                event_evidence
                and match.eligible
                and not source_conflict
                and self.planning == "adaptive"
                and self.mode != "evidence_only"
                and error is None
            ):
                try:
                    self.step("adaptive_plan")
                    plan = await call(
                        "plan",
                        render_planner_prompt(
                            event=event,
                            channel=match.channel,
                            features=selected,
                            missing=match.missing,
                            role=context.role.value,
                            as_of=request.as_of,
                        ),
                        AnalysisPlan,
                    )
                    if plan.channels != [match.channel] or not set(
                        plan.required_fields + plan.if_missing
                    ) <= set(match.missing):
                        raise AppError(
                            ErrorCode.llm_invalid_response, "Plan exceeds supported context", 502
                        )
                    query, refined_query = plan.bank_query, plan.refined_bank_query
                    planner_fields = list(dict.fromkeys(plan.required_fields + plan.if_missing))
                    missing = list(dict.fromkeys(planner_fields + missing))
                    self.checkpoints.append("plan_validated_local_search_only")
                    if plan.next_action == "clarify":
                        error = "clarification_required"
                        self.checkpoints.append("controller_stop:material_conditions_required")
                except AppError as exc:
                    error = exc.code.value
                    if getattr(exc, "usage", None) is not None:
                        model_id, usage_status = exc.usage.model_id, exc.usage.usage_status
                    elif error in {"llm_outcome_unknown", "llm_rate_limited"}:
                        model_id, usage_status = last_model_id, "unknown"
                    if model_id:
                        used_models.add(model_id)
                    self.checkpoints.append("planner_stop:" + error)
            elif self.planning == "code":
                self.checkpoints.append("code_plan_two_call_cap")
            if event_evidence and error != "clarification_required":
                self.budget.search(query)
                self.tools.append("search_bank")
                bank = await self.retriever.search(context, request, query)
                # A result actually selects the next action. Unverified OCR can justify
                # one refinement, never a promotion to applicable bank evidence.
                gap = (
                    "empty"
                    if not bank
                    else "quality_limited"
                    if not any(
                        e.quality.value == "verified" and e.value_kind in {"forecast", "opinion"}
                        for e in bank
                    )
                    else None
                )
                self.checkpoints.append("search_result:" + (gap or "candidate"))
                from app.llm.logging import emit

                emit("retrieval_decision", job_id=self.job_id, session_id=self.session_id or None,
                     gap=gap, evidence_ids=[e.id for e in bank],
                     refinement_available=bool(refined_query and self.runtime.limits.repeated_searches))
                if (
                    gap
                    and refined_query
                    and self.runtime.limits.repeated_searches > 0
                    and " ".join(refined_query.casefold().split())
                    != " ".join(query.casefold().split())
                ):
                    self.budget.search(refined_query)
                    self.tools.append("search_bank")
                    newer = await self.retriever.search(context, request, refined_query)
                    bank = list({e.id: e for e in newer + bank}.values())[:8]
                    self.checkpoints.append("refined_search:" + gap)
                    emit("retrieval_refined", job_id=self.job_id, reason=gap,
                         evidence_ids=[e.id for e in bank])
            if bank:
                self.tool("get_evidence")
                bank = await self.fragments_at(context, request, [e.id for e in bank[:8]])
            if self.bank_context_provider is not None:
                # Internal provenance/quality inspection of this tool result, not an
                # additional model-selected tool. All reads still recheck pinned ACL.
                bank_context = await self.bank_context_provider.describe_bank(
                    context,
                    request,
                    match.channel,
                    [e.id for e in bank],
                    "search_bank" in self.tools,
                )
        evidence = list({e.id: e for e in original_event_evidence + bank}.values())
        event_ids = {e.id for e in event_evidence}
        origins = await self.fragments.origins(context, list(event_ids))
        claims = []
        # A long raw article chunk is not an extracted, verified economic fact.
        # Its original heading is shown; full immutable spans remain in evidence.
        for i, e in enumerate(event_evidence[:3]):
            if e.value_kind == "unknown" or len(e.quote) > 700:
                if claims:
                    continue
                text = event.title
            else:
                text = e.quote
            forecast = e.value_kind in {"forecast", "opinion"}
            claims.append(
                Claim(
                    id=f"event_{i}",
                    kind="hypothesis" if forecast else "fact",
                    origin=origins[e.id],
                    text=text,
                    evidence_ids=[e.id],
                    check="supported"
                    if e.quality.value == "verified" and not forecast
                    else "conditional",
                    conditions=["Это прогноз или мнение источника, а не состоявшийся результат"]
                    if forecast
                    else [],
                )
            )
        if self.parent:
            pinned_ids = set(self.parent.sections.happened + self.parent.sections.bank_view)
            claims = [c.model_copy(deep=True) for c in self.parent.claims if c.id in pinned_ids]
        self.step("retrieval_and_address_checks")
        # Prefer semantically typed short spans over arbitrary OCR chunks; never cut
        # a quote in the prompt, which could remove its units/horizon/caveat.
        usable_bank_ids = {
            eid for t in bank_context.theses if t.status != "conflicting" for eid in t.evidence_ids
        }
        ordered = [e for e in bank if e.id in usable_bank_ids]
        role_questions = (
            [
                "Какие договоры и платежи требуют проверки казначейством?",
                "Какие допущения бюджета нужно подтвердить до изменения финансового плана?",
            ]
            if context.role.value == "cfo"
            else [
                "Какие инвестиционные решения или коммерческие договоры зависят от этого условия?",
                "Кто подтвердит влияние на устойчивость операций до изменения планов?",
            ]
            if context.role.value == "ceo"
            else [
                "Какие условия компании нужно уточнить для этого события?",
                "Кто отвечает за проверку договоров и планов?",
            ]
        )
        pack = {
            "channel": match.channel,
            "event": [
                (
                    {"id": e.id, "quote": e.quote, "kind": e.value_kind, "quality": e.quality.value}
                    if len(e.quote.encode()) < 800
                    else {
                        "id": e.id,
                        "title": event.title,
                        "kind": e.value_kind,
                        "quality": e.quality.value,
                        "scope": "Только заголовок; большие исходные таблицы не извлечены в проверенные показатели",
                    }
                )
                for e in event_evidence[:1]
            ],
            "features": [{"id": f.id, "name": f.name, "value": f.value} for f in selected],
            "bank_background": [
                {"id": e.id, "quote": e.quote, "kind": e.value_kind, "quality": e.quality.value}
                for e in ordered
                if len(e.quote.encode()) < 600
            ][:1],
            "unknown": missing,
            "planner_focus": planner_fields,
            "role": context.role.value,
            "role_questions": role_questions,
            "question": self.question,
            "assumptions": [a.model_dump(exclude={"schema_version"}) for a in self.assumptions],
        }
        proposals = []
        from app.llm.logging import emit

        emit("analysis_basis", job_id=self.job_id, run_id=run_id,
             session_id=self.session_id or None,
             config_hash=self.runtime.sha256, evidence_ids=[e.id for e in evidence],
             feature_ids=[f.id for f in selected], missing_fields=missing)
        if (
            event_evidence
            and match.eligible
            and not source_conflict
            and relevant_channel
            and self.mode != "evidence_only"
            and error is None
        ):
            try:
                draft = await call(
                    "draft",
                    "Предложи до шести кратких условных гипотез на русском: риск и возможность в consequences "
                    "(impact_kind risk/opportunity), отдельную relevance при полезном дополнении, затем проверяемое направление действий в actions. "
                    "Только наблюдаемые признаки и событие, без чисел и утверждений от имени банка. "
                    "Не утверждай рост платежей. Все неизвестные условия существенны. "
                    "User_declared — допущения, не факты; учитывай их ограничения. "
                    "Банковские OCR-кандидаты — только датированный непроверенный фон, не применимые тезисы. "
                    "Каждая гипотеза обязана ссылаться на event; bank_background допустим дополнительно. "
                    "Событие и признаки не раскрывают денежный эффект. Учитывай роль в действиях, не меняя факты. "
                    "effect_window=after_fixed_until допустим только при явном fixed_until пользователя, "
                    "с условием «после окончания фиксации»; не утверждай эффект во время фиксации. "
                    "Не выдумывай эффект при фиксированном договоре. Неизменная ключевая ставка сама "
                    "по себе не создаёт нового риска пересмотра фиксированного договора. "
                    "При отсутствии доказанного эффекта можно вернуть пустой consequences. "
                    "В actions отвечай на вопрос проверяемым действием или уточнением существенного "
                    "условия, объясняя связь с событием; это не утверждение о наступившем эффекте. "
                    "Условия user_declared используй условно, не повторяя даты и числа в тексте. "
                    "Данные: " + compact(pack),
                    Draft,
                )
                self.step("draft_code_checks")
                proposed = candidates(
                    draft.proposals,
                    event_ids | usable_bank_ids,
                    selected,
                    self.assumptions,
                    required_event_ids=event_ids, channel=match.channel,
                )
                emit("draft_code_filter", job_id=self.job_id,
                     rejections=[{"index": i, "reason": reason}
                                 for i, p in enumerate(draft.proposals)
                                 if (reason := candidate_rejection(p, event_ids | usable_bank_ids,
                                                                   selected, event_ids,
                                                                   channel=match.channel,
                                                                   assumptions=self.assumptions))])
                if proposed:
                    verdict = await call(
                        "verify",
                        "Независимо проверь смысл гипотез: причинность, "
                        "субъект, условия, фиксированную ставку/user_declared, отсутствие подмены "
                        "прогноза фактом/ростом платежей и численного эффекта. Без доказанного канала "
                        "верни unsupported; при противоречии conflicting. Допустимо только conditional. "
                        "Проверяемое действие или вопрос о договоре не равны утверждению об изменении платежа. "
                        "Неизменная ключевая ставка не доказывает новый риск незапланированного "
                        "пересмотра фиксированного договора; такой причинный переход unsupported. "
                        "User_declared можно использовать как явно условную предпосылку, не как "
                        "проверенный факт. Сохраняй исходные index из proposals без перенумерации, "
                        "даже если они начинаются не с нуля или имеют пропуски. "
                        "Каждый переданный index ровно один раз. reason — одна короткая фраза "
                        "до ста символов; не пересказывай гипотезу. Данные: "
                        + compact(pack)
                        + "\nproposals="
                        + compact([{"index": i, **p.model_dump()} for i, p in proposed]),
                        Verification,
                    )
                    proposals = verified_proposals(proposed, verdict.verdicts)
                    kept = {i for i, _ in proposals}
                    emit("claim_verification", job_id=self.job_id,
                         decisions=[v.model_dump() for v in verdict.verdicts])
                    self.checkpoints.extend(
                        f"claim_{i}_removed" for i, _ in proposed if i not in kept
                    )
                    self.step("semantic_check_no_repair")
            except AppError as exc:
                error = exc.code.value
                if getattr(exc, "usage", None) is not None:
                    model_id, usage_status = exc.usage.model_id, exc.usage.usage_status
                elif error in {"llm_outcome_unknown", "llm_rate_limited"}:
                    model_id, usage_status = last_model_id, "unknown"
                if model_id:
                    used_models.add(model_id)
                # No blind retry, fallback, repair, or promotion of a saved draft.
                proposals = []
                self.step("gateway_stop:" + error)
        sections = Sections(
            happened=list(self.parent.sections.happened) if self.parent else [c.id for c in claims],
            relevance=[],
            consequences=[],
            actions=[],
            bank_view=list(self.parent.sections.bank_view) if self.parent else [],
            basis=[],
        )
        if match.eligible and event_evidence and not source_conflict:
            cl = Claim(
                id="observed_channel",
                kind="hypothesis",
                origin="rule",
                text=match.mechanism,
                evidence_ids=[event_evidence[0].id],
                feature_ids=[f.id for f in selected],
                conditions=[
                    "Связь по наблюдаемым данным — условие для проверки, не оценка денежного эффекта"
                ],
                check="conditional",
            )
            claims.append(cl)
            sections.relevance.append(cl.id)
        if not self.parent:
            by_id = {e.id: e for e in bank}
            for i, thesis in enumerate(bank_context.theses):
                cited = [by_id[eid] for eid in thesis.evidence_ids if eid in by_id]
                if (thesis.status != "applicable" or thesis.issued_at is None
                        or len(cited) != len(thesis.evidence_ids) or not cited
                        or any(e.quality.value != "verified" or e.value_kind not in {"forecast", "opinion"}
                               for e in cited)
                        or not any(thesis.text == e.quote for e in cited)):
                    continue
                claim = Claim(id=f"bank_{i}", kind="bank_view", origin="bank_analysis",
                              text=thesis.text, evidence_ids=thesis.evidence_ids, check="conditional",
                              conditions=["Позиция банка от " + thesis.issued_at.isoformat(),
                                          "Прогноз или мнение, не факт и не персональная оценка договора",
                                          "Горизонт: " + (thesis.horizon or "не указан")])
                claims.append(claim)
                sections.bank_view.append(claim.id)
        for i, p in proposals:
            cl = Claim(
                id=f"hypothesis_{i}",
                kind="hypothesis",
                impact_kind=p.impact_kind,
                origin="model",
                text=p.text,
                evidence_ids=p.evidence_ids,
                feature_ids=p.feature_ids,
                conditions=p.conditions
                + ["Условие пользователя: " + a.name + "=" + a.value for a in self.assumptions],
                alternatives=p.alternatives,
                check="conditional",
            )
            claims.append(cl)
            getattr(sections, p.section).append(cl.id)
        for i, a in enumerate(self.assumptions):
            cl = Claim(
                id=f"assumption_{i}",
                kind="hypothesis",
                origin="user_declared",
                text=a.name + ": " + a.value,
                check="conditional",
                conditions=["Сообщено пользователем; договор независимо не проверен"],
            )
            claims.append(cl)
            sections.basis.append(cl.id)
        await self.profiles.get_profile(context, context.company_id)
        self.step("final_scope_check_and_stop")
        source_ids = sorted(
            {profile.source_version_id, event.source_version_id}
            | {e.source_version_id for e in evidence}
        )
        limits = (
            "OCR и численные фрагменты не проверены человеком. Банковские кандидаты — фон; "
            "применимый тезис банка не подтверждён. Размер эффекта и условия договоров неизвестны."
        )
        if len(used_models) > 1:
            limits += (
                " Использованы разные модели этапов; точные model_id сохранены в трассе и ledger."
            )
        if error == "clarification_required":
            limits += " Сначала уточните существенные условия из списка вопросов; модельный вывод пока не сформирован."
        if error:
            limits += " Генерация остановлена: " + error + "; автоматического повтора нет."
        if any(a.name == "contract_rate_type" and a.value == "fixed" for a in self.assumptions):
            limits += " Последствия во время фиксации ставки не установлены; период после неё требует явного fixed_until."
        if not match.eligible:
            limits += " Недостаточно данных о финансовой экспозиции клиента."
        delivery_mode = (
            self.mode
            if proposals and self.mode == "controlled"
            else ("live" if proposals else "evidence_only")
        )
        return Analysis(
            id=self.job_id,
            job_id=self.job_id,
            run_id=run_id,
            company_id=context.company_id,
            company_snapshot_id=profile.snapshot_id,
            event_id=event.id,
            event_title=event.title,
            context=context,
            source_version_ids=source_ids,
            domain=event.domain,
            status="partial"
            if claims and match.eligible and not source_conflict
            else "insufficient_evidence",
            delivery=DeliveryInfo(mode=delivery_mode, generated_at=datetime.now(UTC)),
            temporal=temporal,
            model_id=next(iter(used_models)) if len(used_models) == 1 else None,
            usage_status=usage_status,
            policy_version=ENGINE_POLICY_VERSION,
            sections=sections,
            claims=claims,
            evidence=evidence,
            features=features,
            notices=[
                Notice(
                    id="investment",
                    kind="investment_disclaimer",
                    text="Не является индивидуальной инвестиционной рекомендацией",
                ),
                Notice(
                    id="ai",
                    kind="ai_attribution",
                    text="Гипотезы модели — не позиция банка. " + limits,
                ),
                Notice(
                    id="time",
                    kind="temporal_limitation",
                    text="Исторический кейс и текущие новости разделены. Snapshot — не backtest. "
                    "Неизвестные даты доступности не восстановлены.",
                ),
                *(
                    [
                        Notice(
                            id="controlled",
                            kind="ai_attribution",
                            text="Управляемый offline LLM transport; это не ответ настоящей модели.",
                        )
                    ]
                    if self.mode == "controlled"
                    else []
                ),
            ],
            missing_fields=missing,
            questions=(
                ["Подтвердите сообщённое условие исходным договором."] if self.assumptions else []
            )
            + role_questions,
            bank_evidence_status="conflicting"
            if bank_context.diagnosis == "conflicting"
            else "background_only"
            if bank
            else "not_found",
            bank_context=bank_context,
            assumptions=self.assumptions,
            conversation_id=self.conversation_id,
            parent_analysis_id=self.parent.id if self.parent else None,
        )


class DefaultAnalysisEngine(BoundedAnalysisEngine):
    """Integrated M04: optional model planning + at most one adaptive local search.

    Operator two-call jobs explicitly use code planning; no hidden plan/repair call.
    """

    def __init__(self, *, planning="adaptive", **kwargs):
        super().__init__(planning=planning, **kwargs)
