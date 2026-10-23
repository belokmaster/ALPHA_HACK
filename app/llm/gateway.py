"""Shared, fail-closed gateway. Network I/O never holds a database transaction."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.budgets.service import BudgetLedger, Tariff
from app.core.errors import AppError
from app.llm.config import ALLOWED_MODEL_IDS, LLMConfig
from app.llm.logging import emit
from app.llm.runtime import Mode, RuntimeConfig, load_runtime
from app.llm.session import SessionBudget
from app.llm.tables import attempts, key_gates, llm_runs
from app.llm.transport import ChatTransport, ProviderUsage, input_bound, messages_for
from contracts.v1 import ErrorCode, LLMRequest, LLMResult, ModelUsage

Output = TypeVar("Output", bound=BaseModel)


class GatewayError(AppError):
    """No error is automatically retryable. Known usage may accompany invalid output."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        call_id: str | None = None,
        usage: ModelUsage | None = None,
        model_id: str | None = None,
        status: int = 502,
    ) -> None:
        super().__init__(code, message, status)
        self.call_id = call_id
        self.usage = usage
        self.model_id = model_id if model_id is not None else (usage.model_id if usage else None)
        self.retryable = False


class LLMGateway:
    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        config: LLMConfig | None = None,
        api_key: SecretStr | None = None,
        tariffs: dict[str, Tariff] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        runtime: RuntimeConfig | None = None,
        mode: Mode = "adaptive",
    ):
        self.ledger = ledger
        self.engine = ledger.engine
        self.config = config or LLMConfig()
        self.runtime, self.mode = runtime or load_runtime(), mode
        self._key = api_key or SecretStr("")
        # Derive identity on the server; a caller cannot shard a key's budget/gate.
        self.scope = "key_" + hashlib.sha256(self._key.get_secret_value().encode()).hexdigest()
        self.tariffs = tariffs or {}
        self.session = (
            SessionBudget(
                ledger,
                self.scope,
                self.config.live_session_id,
                self.config.base_url,
                self._key,
            )
            if self.config.live_session_id
            else None
        )
        self.transport = ChatTransport(
            transport,
            base_url=self.config.base_url,
            timeout=self.runtime.timeouts.transport_seconds,
        )

    def _enabled(self) -> None:
        if not self.config.live_enabled:
            raise GatewayError(ErrorCode.backend_unavailable, "Live LLM is disabled", status=503)
        if not self._key.get_secret_value().strip():
            raise GatewayError(
                ErrorCode.backend_unavailable, "LLM key is not configured", status=503
            )

        if any(char.isspace() for char in self._key.get_secret_value()):
            raise GatewayError(
                ErrorCode.validation, "LLM key must not contain whitespace", status=422
            )

    def require_output_cap(self) -> None:
        """Operator acceptance; model/endpoint allowlists remain independently enforced."""
        if not self.config.billable_output_cap_confirmed and self.session is None:
            raise GatewayError(
                ErrorCode.backend_unavailable,
                "Billable output cap risk requires operator acceptance or an authorized live session",
                status=503,
            )

    async def start_run(self, run_id: str, *, job_id: str | None = None) -> None:
        self._enabled()
        self.require_output_cap()
        if self.session:
            await self.session.validate()
        # Reuse the public ID validator; no user-provided quota or deadline.
        LLMRequest(run_id=run_id, call_id="validate", model_id="validate", prompt="validate")
        async with self.engine.begin() as c:
            await c.execute(
                pg_insert(llm_runs)
                .values(
                    id=run_id,
                    scope_id=self.scope,
                    job_id=job_id,
                    deadline_at=self.ledger.clock()
                    + timedelta(seconds=self.runtime.timeouts.run_seconds),
                    runtime_snapshot=self.runtime.snapshot(self.mode),
                    session_id=self.config.live_session_id or None,
                )
                .on_conflict_do_nothing()
            )
            row = (
                (await c.execute(select(llm_runs).where(llm_runs.c.id == run_id))).mappings().one()
            )
            if (
                row["scope_id"] != self.scope
                or row["job_id"] != job_id
                or row["runtime_snapshot"] != self.runtime.snapshot(self.mode)
                or row["session_id"] != (self.config.live_session_id or None)
            ):
                raise GatewayError(ErrorCode.data_conflict, "Run identity differs", status=409)

    async def _prepare(
        self,
        request: LLMRequest,
        tariff: Tariff,
        bound: int,
        digest: str,
    ) -> tuple[Decimal, str | None, datetime]:
        quote = tariff.estimate(input_tokens=bound, max_output_tokens=request.max_output_tokens)
        if quote <= 0:
            raise GatewayError(
                ErrorCode.budget_unknown, "Positive maximum reserve required", status=503
            )
        async with self.engine.begin() as c:
            # Lock order is always key -> run. No lock survives this transaction.
            await c.execute(
                pg_insert(key_gates).values(scope_id=self.scope).on_conflict_do_nothing()
            )
            gate = (
                (
                    await c.execute(
                        select(key_gates)
                        .where(key_gates.c.scope_id == self.scope)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if gate["active_call_id"] is not None:
                raise GatewayError(
                    ErrorCode.llm_busy, "Key has an active or unreconciled call", status=409
                )
            if self.session:
                quote = await self.session.allocate(c, quote)
            run = (
                (
                    await c.execute(
                        select(llm_runs).where(llm_runs.c.id == request.run_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if run is None or run["scope_id"] != self.scope:
                raise GatewayError(
                    ErrorCode.validation, "Start the server run before calling", status=422
                )
            if run["runtime_snapshot"] != self.runtime.snapshot(self.mode):
                raise GatewayError(
                    ErrorCode.data_conflict, "Pinned run configuration differs", status=409
                )
            if run["session_id"] != (self.config.live_session_id or None):
                raise GatewayError(ErrorCode.data_conflict, "Pinned session differs", status=409)
            limits = self.runtime.for_mode(self.mode)
            remaining = (run["deadline_at"] - self.ledger.clock()).total_seconds()
            if remaining <= 0:
                raise GatewayError(ErrorCode.deadline_exceeded, "Run deadline exceeded", status=408)
            prior = (
                await c.execute(select(attempts.c.id).where(attempts.c.id == request.call_id))
            ).first()
            if prior:
                raise GatewayError(
                    ErrorCode.idempotency_conflict, "Call ID was already attempted", status=409
                )
            retry = int(request.purpose in ("retry", "fallback"))
            repair = int(request.purpose == "repair")
            if (
                bound > self.runtime.limits.input_per_call
                or run["models_used"] >= limits.max_model_calls
                or run["input_used"] + bound > limits.input_tokens
                or run["output_used"] + request.max_output_tokens > limits.output_tokens
                or run["retry_used"] + retry > 1
                or run["repair_used"] + repair > 1
                or run["quoted_rub"] + quote > self.runtime.limits.budget_rub
            ):
                raise GatewayError(
                    ErrorCode.budget_exceeded, "Shared run limits exhausted", status=429
                )
            await c.execute(
                insert(attempts).values(
                    id=request.call_id,
                    run_id=request.run_id,
                    scope_id=self.scope,
                    model_id=request.model_id,
                    tariff_version=tariff.version,
                    input_rate=tariff.input_rub_per_million,
                    output_rate=tariff.output_rub_per_million,
                    input_bound=bound,
                    output_bound=request.max_output_tokens,
                    request_sha256=digest,
                    state="preparing",
                    session_id=self.config.live_session_id or None,
                    session_reserve=quote if self.session else None,
                )
            )
            await c.execute(
                update(key_gates)
                .where(key_gates.c.scope_id == self.scope)
                .values(active_call_id=request.call_id)
            )
            await c.execute(
                update(llm_runs)
                .where(llm_runs.c.id == request.run_id)
                .values(
                    models_used=run["models_used"] + 1,
                    input_used=run["input_used"] + bound,
                    output_used=run["output_used"] + request.max_output_tokens,
                    retry_used=run["retry_used"] + retry,
                    repair_used=run["repair_used"] + repair,
                    quoted_rub=run["quoted_rub"] + quote,
                )
            )
            return quote, run["job_id"], run["deadline_at"]

    async def _state(
        self,
        call_id: str,
        state: Literal["preparing", "started", "uncertain", "closed"],
        *,
        release: bool = False,
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(key_gates)
                .where(
                    key_gates.c.scope_id == self.scope,
                    key_gates.c.active_call_id == call_id,
                )
                .values(active_call_id=None if release else call_id)
            )
            await c.execute(
                update(attempts)
                .where(attempts.c.id == call_id, attempts.c.scope_id == self.scope)
                .values(state=state)
            )

    async def _uncertain(self, call_id: str) -> None:
        # If this write fails, the durable 'started' call and active gate still fail closed.
        await self.ledger.mark_uncertain(call_id)
        await self._state(call_id, "uncertain")
        if self.session:
            await self.session.stop()

    async def complete(
        self, request: LLMRequest, response_model: type[Output]
    ) -> LLMResult[Output]:
        started_at = time.monotonic()
        common = dict(
            run_id=request.run_id,
            call_id=request.call_id,
            session_id=self.config.live_session_id or None,
            stage=request.purpose,
            model_id=request.model_id,
            config_hash=self.runtime.sha256,
            output_cap=request.max_output_tokens,
        )
        emit("llm_call_enter", **common)
        try:
            result = await self._complete(request, response_model)
        except BaseException as exc:
            usage = getattr(exc, "usage", None)
            emit(
                "llm_call_exit",
                **common,
                duration_seconds=time.monotonic() - started_at,
                outcome=getattr(exc, "code", type(exc).__name__),
                usage=usage.model_dump(mode="json") if usage else None,
            )
            raise
        emit(
            "llm_call_exit",
            **common,
            duration_seconds=time.monotonic() - started_at,
            outcome="validated",
            usage=result.usage.model_dump(mode="json"),
        )
        return result

    async def _complete(
        self, request: LLMRequest, response_model: type[Output]
    ) -> LLMResult[Output]:
        self._enabled()
        self.require_output_cap()
        if request.model_id not in ALLOWED_MODEL_IDS:
            raise GatewayError(ErrorCode.validation, "Model outside allowlist", status=422)
        if request.purpose not in self.runtime.purposes(self.mode):
            raise GatewayError(
                ErrorCode.budget_exceeded, "Stage disabled; no retry/fallback/repair", status=429
            )
        stage = self.runtime.stage(request.purpose)
        if request.model_id != stage.model_id:
            raise GatewayError(
                ErrorCode.data_conflict, "Stage model differs from runtime", status=409
            )
        if request.max_output_tokens > stage.max_output_tokens:
            raise GatewayError(ErrorCode.budget_exceeded, "Stage output limit exceeded", status=429)
        tariff = self.tariffs.get(request.model_id)
        if tariff is None:
            raise GatewayError(ErrorCode.budget_unknown, "Verified tariff required", status=503)
        tariff.require_verified()
        if tariff.model_id != request.model_id:
            raise GatewayError(ErrorCode.data_conflict, "Tariff model differs", status=409)
        messages = messages_for(request.prompt, response_model)
        bound = input_bound(messages)
        digest = hashlib.sha256(
            json.dumps(
                {"request": request.model_dump(mode="json"), "messages": messages,
                 "reasoning_effort": stage.reasoning_effort},
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        quote, job_id, deadline = await self._prepare(request, tariff, bound, digest)
        emit(
            "llm_reserved",
            run_id=request.run_id,
            job_id=job_id,
            call_id=request.call_id,
            session_id=self.config.live_session_id or None,
            input_bound=bound,
            output_bound=request.max_output_tokens,
            reserve_rub=quote,
        )
        started = False
        reserved = False
        try:
            await self.ledger.reserve(
                request.call_id, amount_rub=quote, tariff=tariff, scope_id=self.scope
            )
            reserved = True
            if self.session:
                await self.session.before_send(request.call_id)
            # Set before await: a lost DB acknowledgement is not permission to retry.
            started = True
            await self.ledger.mark_started(request.call_id, call_id=request.call_id, job_id=job_id)
            if self.session:
                await self.session.mark_sent(request.call_id)
            await self._state(request.call_id, "started")
            seconds = min(
                self.runtime.timeouts.call_seconds, (deadline - self.ledger.clock()).total_seconds()
            )
            if seconds <= 0:
                # No transport was sent, but durable started records are reconciled explicitly.
                raise TimeoutError
            async with asyncio.timeout(seconds):
                status, body = await self.transport.send(
                    key=self._key.get_secret_value(),
                    model_id=request.model_id,
                    messages=messages,
                    max_output_tokens=request.max_output_tokens,
                    reasoning_effort=stage.reasoning_effort,
                )
        except BaseException as exc:
            if started:
                # Shield normal cancellation; hard process death leaves 'started' + gate intact.
                try:
                    await asyncio.shield(self._uncertain(request.call_id))
                except Exception:
                    pass
            else:
                # reserve() may have committed before a connection failure; leave the gate
                # closed in that case rather than guessing that money was not reserved.
                if reserved:
                    await self.ledger.release(request.call_id, known_unused=True)
                if self.session and isinstance(exc, AppError):
                    await self.session.abort_unused(request.call_id)
                    await self.session.stop()
                if isinstance(exc, AppError):
                    await self._state(request.call_id, "closed", release=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, AppError) and not started:
                raise
            raise GatewayError(
                ErrorCode.llm_outcome_unknown,
                "Call outcome requires reconciliation",
                call_id=request.call_id,
                model_id=request.model_id,
            ) from None

        # From this point on, loss/cancellation also leaves the durable gate closed.
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict) or envelope.get("model") != request.model_id:
                raise ValueError("Unidentified provider model")
            usage = ProviderUsage.model_validate(envelope.get("usage"))
        except (ValueError, ValidationError, TypeError):
            await self._uncertain(request.call_id)
            code = ErrorCode.llm_rate_limited if status == 429 else ErrorCode.llm_outcome_unknown
            raise GatewayError(
                code,
                "Provider usage/outcome is unconfirmed",
                call_id=request.call_id,
                model_id=request.model_id,
            ) from None
        actual = tariff.quote(
            input_tokens=usage.prompt_tokens, max_output_tokens=usage.completion_tokens
        )
        try:
            await self.ledger.settle(
                request.call_id,
                actual_cost_rub=actual,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
            )
        except Exception:
            raise GatewayError(
                ErrorCode.llm_outcome_unknown,
                "Settlement requires reconciliation",
                call_id=request.call_id,
                model_id=request.model_id,
            ) from None
        public_usage = ModelUsage(
            model_id=request.model_id,
            usage_status="provider",
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_rub=actual,
        )
        emit(
            "llm_cost_estimate",
            run_id=request.run_id,
            call_id=request.call_id,
            model_id=request.model_id,
            nominal_cost_rub=actual,
            estimated_cost_rub=tariff.estimate(
                input_tokens=usage.prompt_tokens, max_output_tokens=usage.completion_tokens
            ),
            estimate_multiplier=tariff.estimate_multiplier,
            estimate_confirmed_by_owner=tariff.estimate_confirmed,
        )
        choices = envelope.get("choices")
        first_choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        message = first_choice.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        finish = first_choice.get("finish_reason")
        finish = (
            finish
            if isinstance(finish, str)
            and finish in {"stop", "length", "content_filter", "tool_calls"}
            else "unknown"
        )
        emit(
            "llm_provider_summary",
            call_id=request.call_id,
            session_id=self.config.live_session_id or None,
            http_status=status,
            finish_reason=finish,
            refusal=bool(message.get("refusal")) if isinstance(message, dict) else False,
            content_chars=len(content) if isinstance(content, str) else None,
            reasoning_tokens=usage.completion_tokens_details.reasoning_tokens
            if usage.completion_tokens_details
            else None,
        )
        if self.session:
            try:
                await self.session.finish(request.call_id, actual)
            except Exception as exc:
                emit(
                    "llm_cost_pending",
                    call_id=request.call_id,
                    session_id=self.config.live_session_id,
                    error_class=type(exc).__name__,
                    reason=exc.message
                    if isinstance(exc, AppError)
                    else "External cost observation failed",
                )
                await self.session.stop()
                # Usage/model outcome is already known. Keep the gate and session hold
                # for accounting, without relabelling the paid model response unknown.
                await self._state(request.call_id, "started")
                raise GatewayError(
                    ErrorCode.backend_unavailable,
                    "External cost reconciliation required",
                    call_id=request.call_id,
                    usage=public_usage,
                ) from None
        if usage.prompt_tokens > bound or usage.completion_tokens > request.max_output_tokens:
            # Truthful overrun settlement; the key stays blocked pending bound/tariff review.
            raise GatewayError(
                ErrorCode.budget_exceeded,
                "Provider exceeded reserved token envelope",
                call_id=request.call_id,
                usage=public_usage,
                status=429,
            )
        await self._state(request.call_id, "closed", release=True)
        if status != 200:
            code = ErrorCode.llm_rate_limited if status == 429 else ErrorCode.backend_unavailable
            raise GatewayError(
                code,
                "Provider rejected completion",
                call_id=request.call_id,
                usage=public_usage,
                status=502,
            )
        try:
            choice = envelope["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("Incomplete or tool response")
            content = choice["message"]["content"]
            if choice["message"].get("refusal"):
                raise ValueError("Provider refusal")
            value = response_model.model_validate_json(content, strict=True)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            emit("llm_structured_rejected", call_id=request.call_id, finish_reason=finish,
                 validation_error_types=[e["type"] for e in exc.errors(include_input=False)]
                 if isinstance(exc, ValidationError) else [type(exc).__name__],
                 outer_json_fence=isinstance(content, str) and content.strip().startswith("```json\n")
                 and content.strip().endswith("\n```"))
            raise GatewayError(
                ErrorCode.llm_invalid_response,
                "Structured response validation failed",
                call_id=request.call_id,
                usage=public_usage,
            ) from None
        # Pydantic needs the concrete class at runtime to serialize the full validated value.
        return LLMResult[response_model](  # type: ignore[valid-type]
            call_id=request.call_id, value=value, usage=public_usage
        )

    async def reconcile(self, call_id: str, *, input_tokens: int, output_tokens: int) -> ModelUsage:
        """Operator-only: use independently confirmed usage, never inferred zero/timeout.

        There is deliberately no automatic reconciliation or public HTTP endpoint.
        """
        self._enabled()
        confirmed = ProviderUsage(prompt_tokens=input_tokens, completion_tokens=output_tokens)
        input_tokens, output_tokens = confirmed.prompt_tokens, confirmed.completion_tokens
        async with self.engine.connect() as c:
            row = (
                (
                    await c.execute(
                        select(attempts).where(
                            attempts.c.id == call_id, attempts.c.scope_id == self.scope
                        )
                    )
                )
                .mappings()
                .one()
            )
        tariff = Tariff(
            model_id=row["model_id"],
            version=row["tariff_version"],
            input_rub_per_million=row["input_rate"],
            output_rub_per_million=row["output_rate"],
            verified=True,
        )
        cost = tariff.quote(input_tokens=input_tokens, max_output_tokens=output_tokens)
        await self.ledger.settle(
            call_id, actual_cost_rub=cost, input_tokens=input_tokens, output_tokens=output_tokens
        )
        if self.session:
            await self.session.finish(call_id, cost)
        await self._state(call_id, "closed", release=True)
        return ModelUsage(
            model_id=row["model_id"],
            usage_status="provider",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_rub=cost,
        )
