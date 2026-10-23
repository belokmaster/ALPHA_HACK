from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.budgets.tables import budget_days, model_calls, reservations
from app.core.errors import AppError
from app.llm.config import ALLOWED_MODEL_IDS
from contracts.v1 import BudgetReservation, ErrorCode

MONEY_PRECISION = Decimal("0.000001")
MOSCOW = ZoneInfo("Europe/Moscow")


def utc_now() -> datetime:
    return datetime.now(UTC)


def budget_day_at(now: datetime) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Budget clock must return a timezone-aware datetime")
    return (now.astimezone(MOSCOW) - timedelta(hours=3)).date()


def money(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0:
        raise AppError(ErrorCode.validation, "Cost must be a finite nonnegative Decimal", 422)
    return value.quantize(MONEY_PRECISION, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Tariff:
    model_id: str
    version: str
    input_rub_per_million: Decimal | None = None
    output_rub_per_million: Decimal | None = None
    verified: bool = False
    estimate_multiplier: Decimal = Decimal("1")
    estimate_confirmed: bool = False

    def require_verified(self) -> None:
        if self.model_id not in ALLOWED_MODEL_IDS:
            raise AppError(ErrorCode.validation, "Model is outside the configured allowlist", 422)
        if (
            not self.verified
            or not self.version
            or self.input_rub_per_million is None
            or self.output_rub_per_million is None
        ):
            raise AppError(ErrorCode.budget_unknown, "Provider tariff has not been verified", 503)
        money(self.input_rub_per_million)
        money(self.output_rub_per_million)
        if not self.estimate_multiplier.is_finite() or self.estimate_multiplier < 1:
            raise AppError(ErrorCode.budget_unknown, "Invalid cost estimate multiplier", 503)

    def quote(self, *, input_tokens: int, max_output_tokens: int) -> Decimal:
        self.require_verified()
        if input_tokens < 0 or max_output_tokens < 0:
            raise AppError(ErrorCode.validation, "Token limits must be nonnegative", 422)
        assert self.input_rub_per_million is not None
        assert self.output_rub_per_million is not None
        return money(
            (
                input_tokens * self.input_rub_per_million
                + max_output_tokens * self.output_rub_per_million
            )
            / Decimal(1_000_000)
        )


    def estimate(self, *, input_tokens: int, max_output_tokens: int) -> Decimal:
        """Owner-accepted projection; never a provider charge or replacement for usage."""
        self.require_verified()
        if input_tokens < 0 or max_output_tokens < 0:
            raise AppError(ErrorCode.validation, "Token limits must be nonnegative", 422)
        assert self.input_rub_per_million is not None
        assert self.output_rub_per_million is not None
        return money(
            (input_tokens * self.input_rub_per_million
             + max_output_tokens * self.output_rub_per_million)
            * self.estimate_multiplier / Decimal(1_000_000)
        )


class BudgetLedger:
    """All consumers of one provider key must use one server-selected scope_id.

    Transactions serialize on a budget-day row. Reservations never expire automatically:
    a crashed request may have reached the provider. A provider-confirmed settlement can
    reconcile uncertain calls; neither lease recovery nor reset releases their reserve.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Callable[[], datetime] = utc_now,
        internal_cap_rub: Decimal = Decimal("450"),
        provider_cap_rub: Decimal = Decimal("500"),
    ) -> None:
        self.engine = engine
        self.clock = clock
        self.internal_cap = money(internal_cap_rub)
        self.provider_cap = money(provider_cap_rub)
        if not 0 < self.internal_cap <= min(Decimal("450"), self.provider_cap) <= Decimal("500"):
            raise ValueError("Internal/provider caps must be bounded by 450/500 RUB")
        if self.provider_cap > 500:
            raise ValueError("Provider cap cannot exceed 500 RUB")

    @staticmethod
    def _dto(row: RowMapping) -> BudgetReservation:
        return BudgetReservation(
            id=row["id"],
            budget_day=row["budget_day"],
            amount_rub=row["amount_rub"],
            status=row["status"],
            tariff_version=row["tariff_version"],
        )

    async def reserve(
        self,
        reservation_id: str,
        *,
        amount_rub: Decimal,
        tariff: Tariff,
        scope_id: str = "team_key",
    ) -> BudgetReservation:
        tariff.require_verified()
        amount = money(amount_rub)
        if amount <= 0:
            raise AppError(ErrorCode.validation, "Reservation amount must be positive", 422)
        if not reservation_id or not scope_id:
            raise AppError(
                ErrorCode.validation, "Reservation and budget scope IDs are required", 422
            )
        now = self.clock()
        day = budget_day_at(now)
        async with self.engine.begin() as connection:
            await connection.execute(
                pg_insert(budget_days)
                .values(
                    scope_id=scope_id,
                    budget_day=day,
                    internal_cap_rub=self.internal_cap,
                    provider_cap_rub=self.provider_cap,
                )
                .on_conflict_do_nothing()
            )
            day_row = (
                (
                    await connection.execute(
                        select(budget_days)
                        .where(
                            budget_days.c.scope_id == scope_id,
                            budget_days.c.budget_day == day,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                day_row["internal_cap_rub"] != self.internal_cap
                or day_row["provider_cap_rub"] != self.provider_cap
            ):
                raise AppError(
                    ErrorCode.data_conflict, "Budget policy differs for this key/day", 409
                )
            prior = (
                (
                    await connection.execute(
                        select(reservations).where(reservations.c.id == reservation_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                if (
                    prior["scope_id"] != scope_id
                    or prior["budget_day"] != day
                    or prior["amount_rub"] != amount
                    or prior["tariff_version"] != tariff.version
                    or prior["model_id"] != tariff.model_id
                ):
                    raise AppError(
                        ErrorCode.idempotency_conflict, "Reservation ID already used", 409
                    )
                return self._dto(prior)
            from app.llm.tables import external_expenses

            external = await connection.scalar(
                select(func.coalesce(func.sum(external_expenses.c.amount_rub), 0)).where(
                    external_expenses.c.scope_id == scope_id,
                    external_expenses.c.budget_day == day,
                )
            )
            total = day_row["spent_rub"] + day_row["reserved_rub"] + amount + external
            if total > self.internal_cap or total > self.provider_cap:
                raise AppError(ErrorCode.budget_exceeded, "Shared daily budget exhausted", 429)
            row = (
                (
                    await connection.execute(
                        pg_insert(reservations)
                        .values(
                            id=reservation_id,
                            scope_id=scope_id,
                            budget_day=day,
                            model_id=tariff.model_id,
                            tariff_version=tariff.version,
                            amount_rub=amount,
                            status="reserved",
                            created_at=now,
                            updated_at=now,
                        )
                        .on_conflict_do_nothing(index_elements=[reservations.c.id])
                        .returning(reservations)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AppError(ErrorCode.idempotency_conflict, "Reservation ID already used", 409)
            await connection.execute(
                update(budget_days)
                .where(
                    budget_days.c.scope_id == scope_id,
                    budget_days.c.budget_day == day,
                )
                .values(reserved_rub=budget_days.c.reserved_rub + amount)
            )
            return self._dto(row)

    async def _locked_reservation(
        self,
        connection: AsyncConnection,
        reservation_id: str,
    ) -> RowMapping:
        row = (
            (
                await connection.execute(
                    select(reservations).where(reservations.c.id == reservation_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AppError(ErrorCode.not_found, "Budget reservation not found", 404)
        # Same lock order as reserve: day, then reservation. A refund cannot race a reserve.
        await connection.execute(
            select(budget_days)
            .where(
                budget_days.c.scope_id == row["scope_id"],
                budget_days.c.budget_day == row["budget_day"],
            )
            .with_for_update()
        )
        return (
            (
                await connection.execute(
                    select(reservations)
                    .where(reservations.c.id == reservation_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )

    async def mark_started(
        self,
        reservation_id: str,
        *,
        call_id: str,
        job_id: str | None = None,
    ) -> None:
        """Durably record BEFORE transport; a started call is unsafe to retry after a crash."""
        async with self.engine.begin() as connection:
            row = await self._locked_reservation(connection, reservation_id)
            if row["status"] != "reserved":
                raise AppError(
                    ErrorCode.data_conflict, "Reservation cannot start another call", 409
                )
            existing = (
                await connection.execute(
                    select(model_calls.c.id).where(
                        model_calls.c.reservation_id == reservation_id,
                    )
                )
            ).first()
            if existing is not None:
                raise AppError(
                    ErrorCode.data_conflict, "Model call already started; do not retry", 409
                )
            await connection.execute(
                insert(model_calls).values(
                    id=call_id,
                    reservation_id=reservation_id,
                    job_id=job_id,
                    model_id=row["model_id"],
                    outcome="started",
                    usage_status="unknown",
                    started_at=self.clock(),
                )
            )

    async def mark_uncertain(self, reservation_id: str) -> BudgetReservation:
        async with self.engine.begin() as connection:
            row = await self._locked_reservation(connection, reservation_id)
            if row["status"] not in ("reserved", "uncertain"):
                raise AppError(
                    ErrorCode.data_conflict, "Closed reservation cannot become uncertain", 409
                )
            call = (
                await connection.execute(
                    select(model_calls.c.id).where(
                        model_calls.c.reservation_id == reservation_id,
                        model_calls.c.outcome.in_(("started", "uncertain")),
                    )
                )
            ).first()
            if call is None:
                raise AppError(ErrorCode.data_conflict, "No started call to mark uncertain", 409)
            await connection.execute(
                update(model_calls)
                .where(
                    model_calls.c.reservation_id == reservation_id,
                )
                .values(outcome="uncertain", usage_status="unknown", finished_at=self.clock())
            )
            updated = (
                (
                    await connection.execute(
                        update(reservations)
                        .where(
                            reservations.c.id == reservation_id,
                        )
                        .values(status="uncertain", updated_at=self.clock())
                        .returning(reservations)
                    )
                )
                .mappings()
                .one()
            )
            return self._dto(updated)

    async def settle(
        self,
        reservation_id: str,
        *,
        actual_cost_rub: Decimal,
        input_tokens: int,
        output_tokens: int,
    ) -> BudgetReservation:
        """Only provider-confirmed usage belongs here, including a confirmed zero charge.

        An unexpected cost above the reserve is recorded truthfully, blocking future reserves
        if the cap is exceeded; it is never clipped. Live transport must reserve a true maximum.
        """
        actual = money(actual_cost_rub)
        if input_tokens < 0 or output_tokens < 0:
            raise AppError(ErrorCode.validation, "Provider usage must be nonnegative", 422)
        async with self.engine.begin() as connection:
            row = await self._locked_reservation(connection, reservation_id)
            call = (
                (
                    await connection.execute(
                        select(model_calls).where(
                            model_calls.c.reservation_id == reservation_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row["status"] == "settled" and call is not None:
                if (
                    call["cost_rub"] == actual
                    and call["input_tokens"] == input_tokens
                    and call["output_tokens"] == output_tokens
                ):
                    return self._dto(row)
                raise AppError(
                    ErrorCode.idempotency_conflict, "Settlement differs from prior usage", 409
                )
            if row["status"] not in ("reserved", "uncertain") or call is None:
                raise AppError(ErrorCode.data_conflict, "No open model call to settle", 409)
            await connection.execute(
                update(model_calls)
                .where(
                    model_calls.c.reservation_id == reservation_id,
                )
                .values(
                    outcome="known",
                    usage_status="provider",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_rub=actual,
                    finished_at=self.clock(),
                )
            )
            await connection.execute(
                update(budget_days)
                .where(
                    budget_days.c.scope_id == row["scope_id"],
                    budget_days.c.budget_day == row["budget_day"],
                )
                .values(
                    reserved_rub=budget_days.c.reserved_rub - row["amount_rub"],
                    spent_rub=budget_days.c.spent_rub + actual,
                )
            )
            updated = (
                (
                    await connection.execute(
                        update(reservations)
                        .where(
                            reservations.c.id == reservation_id,
                        )
                        .values(
                            status="settled",
                            actual_cost_rub=actual,
                            updated_at=self.clock(),
                        )
                        .returning(reservations)
                    )
                )
                .mappings()
                .one()
            )
            return self._dto(updated)

    async def release(self, reservation_id: str, *, known_unused: bool) -> BudgetReservation:
        if not known_unused:
            raise AppError(ErrorCode.budget_unknown, "Unknown usage cannot release a reserve", 409)
        async with self.engine.begin() as connection:
            row = await self._locked_reservation(connection, reservation_id)
            if row["status"] == "released":
                return self._dto(row)
            started = (
                await connection.execute(
                    select(model_calls.c.id).where(
                        model_calls.c.reservation_id == reservation_id,
                    )
                )
            ).first()
            if row["status"] != "reserved" or started is not None:
                raise AppError(
                    ErrorCode.budget_unknown,
                    "Started or uncertain calls require confirmed settlement",
                    409,
                )
            await connection.execute(
                update(budget_days)
                .where(
                    budget_days.c.scope_id == row["scope_id"],
                    budget_days.c.budget_day == row["budget_day"],
                )
                .values(reserved_rub=budget_days.c.reserved_rub - row["amount_rub"])
            )
            updated = (
                (
                    await connection.execute(
                        update(reservations)
                        .where(
                            reservations.c.id == reservation_id,
                        )
                        .values(status="released", updated_at=self.clock())
                        .returning(reservations)
                    )
                )
                .mappings()
                .one()
            )
            return self._dto(updated)

    async def has_unknown_outcome(self, job_id: str) -> bool:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                select(model_calls.c.id)
                .where(
                    and_(
                        model_calls.c.job_id == job_id,
                        model_calls.c.outcome.in_(("started", "uncertain")),
                    )
                )
                .limit(1)
            )
            return result.first() is not None
