"""One explicit service authorization. No automatic creation, reopening or reset.

4x calibrated estimate / minimum 1 RUB reservation and 5 RUB unspent headroom are risk margins,
NOT verified prices or an absolute provider output guarantee. Key gate serializes
account observations with model dispatch. Uncertain holds replace, not add to, spend.
"""

import asyncio
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from app.budgets.service import BudgetLedger, budget_day_at, money
from app.budgets.tables import budget_days
from app.core.errors import AppError
from app.llm.config import ALLOWED_BASE_URLS
from app.llm.logging import emit
from app.llm.tables import attempts, external_expenses, key_gates, sessions
from contracts.v1 import ErrorCode

RISK = "owner-2026-10-23: unconfirmed output cap and tariff/spend mismatch accepted"
HEADROOM = Decimal("5")
EPSILON = Decimal("0.00000001")
SPEND_POLL_ATTEMPTS = 15
SPEND_POLL_INTERVAL = 4.0
SPEND_POLL_SECONDS = 70


def risk_reserve(quote: Decimal) -> Decimal:
    return money(max(Decimal("1"), quote * 4))


async def account_snapshot(base_url: str, key: SecretStr) -> dict[str, Decimal]:
    if base_url not in ALLOWED_BASE_URLS:
        raise ValueError("Endpoint outside allowlist")
    try:
        async with httpx.AsyncClient(
            timeout=5,
            follow_redirects=False,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0),
        ) as client:
            response = await client.get(
                base_url.removesuffix("/v1") + "/key/info",
                headers={"Authorization": "Bearer " + key.get_secret_value()},
            )
            response.raise_for_status()
            info = response.json(parse_float=Decimal)["info"]
            if info.get("blocked"):
                raise ValueError("Account blocked")
            result = Decimal(str(info["spend"]))
            total = Decimal(str(info["total_spend"]))
            cap = Decimal(str(info["max_budget"]))
            if (
                not all(v.is_finite() for v in (result, total, cap))
                or result < 0
                or total < 0
                or cap <= 0
            ):
                raise ValueError("Account counters ambiguous")
            return {
                "spend": result,
                "total_spend": total,
                "max_budget": cap,
                "remaining": cap - result,
            }
    except Exception:
        # Never stringify request/response, headers, provider body or secret.
        raise AppError(
            ErrorCode.backend_unavailable, "External spend unavailable/ambiguous", 503
        ) from None


async def account_spend(base_url: str, key: SecretStr) -> Decimal:
    snapshot = await account_snapshot(base_url, key)
    # spend is the current-day counter; total_spend survives its reset.
    # Compare session deltas using historical spend; keep daily balance checks separate.
    if snapshot["remaining"] <= HEADROOM + 1:
        raise AppError(ErrorCode.budget_exceeded, "Account balance/headroom insufficient", 429)
    return snapshot["total_spend"]


class SessionBudget:
    def __init__(
        self, ledger: BudgetLedger, scope: str, session_id: str, base_url: str, key: SecretStr
    ) -> None:
        self.ledger, self.engine = ledger, ledger.engine
        self.scope, self.id, self.base_url, self.key = scope, session_id, base_url, key

    async def locked(self, c: AsyncConnection) -> RowMapping:
        row = (
            (
                await c.execute(
                    select(sessions)
                    .where(
                        sessions.c.id == self.id,
                        sessions.c.scope_id == self.scope,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AppError(ErrorCode.forbidden, "Explicit live session required", 403)
        if (
            row["state"] != "open"
            or row["base_url"] != self.base_url
            or row["budget_day"] != budget_day_at(self.ledger.clock())
            or row["risk_acceptance"] != RISK
        ):
            raise AppError(ErrorCode.forbidden, "Live session closed, expired or mismatched", 403)
        return row

    async def validate(self) -> None:
        async with self.engine.begin() as c:
            await self.locked(c)

    async def open(
        self,
        *,
        expected_spend: Decimal,
        max_calls: int,
        budget_rub: Decimal,
        historical_external: Decimal = Decimal("0"),
        authorization_id: str | None = None,
    ) -> None:
        if (
            not self.id
            or len(self.id) > 128
            or not 1 <= max_calls <= 30
            or not budget_rub.is_finite()
            or not HEADROOM < budget_rub <= 50
            or not historical_external.is_finite()
            or historical_external < 0
        ):
            raise ValueError("Session limits outside owner authorization")
        if historical_external:
            # The two manual probes are already recorded in the historical ledger.
            # Never assign that key/date-specific expense to a new authorization.
            raise ValueError("Historical external import is no longer supported")
        observed = await account_spend(self.base_url, self.key)
        if abs(observed - expected_spend) > EPSILON:
            raise AppError(
                ErrorCode.data_conflict,
                "Unexpected baseline spend; investigate before opening",
                409,
            )
        async with self.engine.begin() as c:
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
            if gate["active_call_id"]:
                raise AppError(ErrorCode.llm_busy, "Active/uncertain key gate", 409)
            prior = (
                (await c.execute(select(sessions).where(sessions.c.id == self.id)))
                .mappings()
                .one_or_none()
            )
            if prior:
                # Explicit repeat can inspect the same permission, never reopen/reset it.
                if (
                    prior["scope_id"] != self.scope
                    or prior["max_calls"] != max_calls
                    or prior["budget_rub"] != budget_rub
                    or prior["baseline_spend"] != observed
                    or (authorization_id is not None and prior["authorization_id"] != authorization_id)
                ):
                    raise AppError(ErrorCode.data_conflict, "Session identity already used", 409)
                return
            if (not authorization_id or len(authorization_id) > 128
                    or authorization_id.startswith("legacy:") or not authorization_id.strip()):
                raise AppError(ErrorCode.validation, "Explicit new owner authorization ID required", 422)
            existing = (await c.execute(select(sessions.c.id).where(
                sessions.c.scope_id == self.scope,
                ((sessions.c.authorization_id == authorization_id)
                 | sessions.c.state.in_(["open", "blocked"])
                 | (sessions.c.reserved_rub > 0)),
            ))).first()
            if existing:
                raise AppError(ErrorCode.forbidden, "Authorization already used or previous session not closed", 403)
            now = self.ledger.clock()
            await c.execute(
                insert(sessions).values(
                    id=self.id,
                    scope_id=self.scope,
                    budget_day=budget_day_at(now),
                    base_url=self.base_url,
                    state="open",
                    max_calls=max_calls,
                    budget_rub=budget_rub,
                    baseline_spend=observed,
                    last_spend=observed,
                    risk_acceptance=RISK,
                    authorization_id=authorization_id,
                    created_at=now,
                )
            )

    async def allocate(self, c: AsyncConnection, quote: Decimal) -> Decimal:
        row = await self.locked(c)
        reserve = risk_reserve(quote)
        if (
            row["calls_allocated"] >= row["max_calls"]
            or row["spent_rub"] + row["reserved_rub"] + reserve > row["budget_rub"] - HEADROOM
        ):
            raise AppError(
                ErrorCode.budget_exceeded, "Service session limit/headroom exhausted", 429
            )
        await c.execute(
            update(sessions)
            .where(sessions.c.id == self.id)
            .values(
                calls_allocated=row["calls_allocated"] + 1,
                reserved_rub=row["reserved_rub"] + reserve,
            )
        )
        return reserve

    async def before_send(self, call_id: str) -> None:
        observed = await account_spend(self.base_url, self.key)
        async with self.engine.begin() as c:
            row = await self.locked(c)
            if abs(observed - row["last_spend"]) > EPSILON:
                raise AppError(
                    ErrorCode.data_conflict, "Unattributed external spend; stop live session", 409
                )
            await c.execute(
                update(attempts).where(attempts.c.id == call_id).values(external_before=observed)
            )

    async def mark_sent(self, call_id: str) -> None:
        async with self.engine.begin() as c:
            row = await self.locked(c)
            call = (
                (await c.execute(select(attempts).where(attempts.c.id == call_id))).mappings().one()
            )
            if call["sent_at"] is not None:
                raise AppError(ErrorCode.idempotency_conflict, "Already dispatched", 409)
            await c.execute(
                update(attempts).where(attempts.c.id == call_id).values(sent_at=self.ledger.clock())
            )
            await c.execute(
                update(sessions)
                .where(sessions.c.id == self.id)
                .values(calls_sent=row["calls_sent"] + 1)
            )

    async def abort_unused(self, call_id: str) -> None:
        async with self.engine.begin() as c:
            row = (
                (
                    await c.execute(
                        select(sessions).where(sessions.c.id == self.id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            call = (
                (await c.execute(select(attempts).where(attempts.c.id == call_id))).mappings().one()
            )
            if call["sent_at"] is not None:
                raise AppError(
                    ErrorCode.data_conflict, "Cannot release a dispatched session call", 409
                )
            await c.execute(
                update(sessions)
                .where(sessions.c.id == self.id)
                .values(
                    calls_allocated=row["calls_allocated"] - 1,
                    reserved_rub=row["reserved_rub"] - call["session_reserve"],
                )
            )

    async def finish(self, call_id: str, actual: Decimal) -> None:
        async with self.engine.connect() as c:
            before = (
                (await c.execute(select(attempts).where(attempts.c.id == call_id))).mappings().one()
            )
        if before["external_after"] is not None:
            return
        # Only free account metadata is polled. Never resend a model completion.
        async with asyncio.timeout(SPEND_POLL_SECONDS):
            for poll in range(SPEND_POLL_ATTEMPTS):
                observed = await account_spend(self.base_url, self.key)
                emit(
                    "llm_spend_observation",
                    session_id=self.id,
                    call_id=call_id,
                    poll=poll,
                    spend=observed,
                    before=before["external_before"],
                )
                if abs(observed - before["external_before"]) > EPSILON or actual == 0:
                    break
                if poll < SPEND_POLL_ATTEMPTS - 1:
                    await asyncio.sleep(SPEND_POLL_INTERVAL)
        async with self.engine.begin() as c:
            row = (
                (
                    await c.execute(
                        select(sessions).where(sessions.c.id == self.id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            # Charge the original reservation day even if the HTTP crossed midnight UTC.
            await c.execute(
                select(budget_days)
                .where(
                    budget_days.c.scope_id == self.scope,
                    budget_days.c.budget_day == row["budget_day"],
                )
                .with_for_update()
            )
            call = (
                (await c.execute(select(attempts).where(attempts.c.id == call_id))).mappings().one()
            )
            if call["external_after"] is not None:
                return  # Settlement replay cannot double-count a known session charge.
            delta = observed - call["external_before"]
            # JSON float rendering vs NUMERIC(24,15) rounding is not a new charge.
            if -EPSILON <= delta < 0:
                observed = call["external_before"]
                delta = Decimal("0")
            if (
                delta < 0
                or (actual > 0 and delta <= 0)
                or max(delta, actual) > call["session_reserve"]
            ):
                raise AppError(
                    ErrorCode.data_conflict,
                    "External charge unknown or exceeds safety reserve",
                    409,
                )
            # Confirmed usage is already settled in the ordinary ledger. Store only the
            # positive difference as an external day adjustment: no fake model calls.
            await c.execute(
                pg_insert(external_expenses)
                .values(
                    id=call_id,
                    scope_id=self.scope,
                    budget_day=row["budget_day"],
                    amount_rub=max(Decimal("0"), delta - actual),
                    reason="Observed gateway spend minus usage tariff; pricing cause unconfirmed",
                )
                .on_conflict_do_nothing()
            )
            await c.execute(
                update(sessions)
                .where(sessions.c.id == self.id)
                .values(
                    spent_rub=row["spent_rub"] + money(max(delta, actual)),
                    reserved_rub=row["reserved_rub"] - call["session_reserve"],
                    last_spend=observed,
                )
            )
            await c.execute(
                update(attempts).where(attempts.c.id == call_id).values(external_after=observed)
            )

    async def stop(self, *, close: bool = False) -> None:
        async with self.engine.begin() as c:
            # Same key -> session lock order as allocation; no reopening even across days.
            await c.execute(
                select(key_gates).where(key_gates.c.scope_id == self.scope).with_for_update()
            )
            await c.execute(
                update(sessions)
                .where(sessions.c.id == self.id, sessions.c.scope_id == self.scope)
                .values(state="closed" if close else "blocked", closed_at=self.ledger.clock())
            )

    async def resume(self, expected_spend: Decimal) -> None:
        """Explicit owner reauthorization, only after fully known accounting; no resets."""
        observed = await account_spend(self.base_url, self.key)
        if abs(observed - expected_spend) > EPSILON:
            raise AppError(ErrorCode.data_conflict, "Resume baseline changed", 409)
        async with self.engine.begin() as c:
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
            row = (
                (
                    await c.execute(
                        select(sessions)
                        .where(
                            sessions.c.id == self.id,
                            sessions.c.scope_id == self.scope,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                gate["active_call_id"]
                or row["state"] != "blocked"
                or row["reserved_rub"] != 0
                or row["last_spend"] != observed
                or row["base_url"] != self.base_url
                or row["budget_day"] != budget_day_at(self.ledger.clock())
                or row["calls_allocated"] >= row["max_calls"]
                or row["spent_rub"] + 1 > row["budget_rub"] - HEADROOM
            ):
                raise AppError(ErrorCode.forbidden, "Session cannot safely resume", 403)
            await c.execute(
                update(sessions)
                .where(sessions.c.id == self.id)
                .values(state="open", closed_at=None)
            )
            emit(
                "llm_session_explicitly_resumed",
                session_id=self.id,
                calls_sent=row["calls_sent"],
                spent_rub=row["spent_rub"],
                observed_spend=observed,
            )

    async def status(self) -> dict[str, Any]:
        async with self.engine.connect() as c:
            row = (
                (
                    await c.execute(
                        select(sessions).where(
                            sessions.c.id == self.id,
                            sessions.c.scope_id == self.scope,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return {k: v for k, v in row.items() if k != "scope_id"}
