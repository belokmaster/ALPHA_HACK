import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.budgets.service import BudgetLedger, Tariff
from app.budgets.tables import budget_days, model_calls
from app.core.errors import AppError
from contracts.v1 import ErrorCode

pytestmark = pytest.mark.integration


def fake_tariff() -> Tariff:
    # Only injected synthetic prices in tests; these are NOT verified provider tariffs.
    return Tariff(
        model_id="deepseek/deepseek-v4.1-flash",
        version="synthetic-test-v1",
        input_rub_per_million=Decimal("1"),
        output_rub_per_million=Decimal("2"),
        verified=True,
    )


async def test_atomic_reservation_race_cannot_exceed_cap(db: AsyncEngine) -> None:
    scope = str(uuid4())
    ledger = BudgetLedger(db)
    results = await asyncio.gather(
        *(
            ledger.reserve(
                str(uuid4()), amount_rub=Decimal("50"), tariff=fake_tariff(), scope_id=scope
            )
            for _ in range(12)
        ),
        return_exceptions=True,
    )
    denied = [item for item in results if isinstance(item, AppError)]
    assert len(denied) == 3
    assert all(item.code == ErrorCode.budget_exceeded for item in denied)
    assert sum(not isinstance(item, BaseException) for item in results) == 9
    async with db.connect() as connection:
        row = (
            (await connection.execute(select(budget_days).where(budget_days.c.scope_id == scope)))
            .mappings()
            .one()
        )
    assert row["reserved_rub"] == Decimal("450")
    assert row["spent_rub"] == 0


async def test_reset_at_three_moscow_preserves_previous_day_reserve(db: AsyncEngine) -> None:
    scope = str(uuid4())
    clock = [datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC)]
    ledger = BudgetLedger(db, clock=lambda: clock[0])
    first = await ledger.reserve(
        str(uuid4()),
        amount_rub=Decimal("450"),
        tariff=fake_tariff(),
        scope_id=scope,
    )
    clock[0] = datetime(2026, 10, 21, 0, 0, 0, tzinfo=UTC)
    second = await ledger.reserve(
        str(uuid4()),
        amount_rub=Decimal("450"),
        tariff=fake_tariff(),
        scope_id=scope,
    )
    assert first.budget_day.isoformat() == "2026-10-20"
    assert second.budget_day.isoformat() == "2026-10-21"
    async with db.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(budget_days.c.reserved_rub).where(budget_days.c.scope_id == scope)
                )
            )
            .scalars()
            .all()
        )
    assert rows == [Decimal("450"), Decimal("450")]


async def test_uncertain_and_started_calls_keep_reserve_until_confirmed(db: AsyncEngine) -> None:
    scope, reservation_id = str(uuid4()), str(uuid4())
    ledger = BudgetLedger(db)
    await ledger.reserve(
        reservation_id, amount_rub=Decimal("450"), tariff=fake_tariff(), scope_id=scope
    )
    await ledger.mark_started(reservation_id, call_id=str(uuid4()))
    with pytest.raises(AppError) as error:
        await ledger.release(reservation_id, known_unused=True)
    assert error.value.code == ErrorCode.budget_unknown
    await ledger.mark_uncertain(reservation_id)
    with pytest.raises(AppError):
        await ledger.release(reservation_id, known_unused=True)
    with pytest.raises(AppError):
        await ledger.mark_started(reservation_id, call_id=str(uuid4()))
    with pytest.raises(AppError) as error:
        await ledger.reserve(
            str(uuid4()), amount_rub=Decimal("1"), tariff=fake_tariff(), scope_id=scope
        )
    assert error.value.code == ErrorCode.budget_exceeded
    result = await ledger.settle(
        reservation_id,
        actual_cost_rub=Decimal("123.4"),
        input_tokens=100,
        output_tokens=20,
    )
    assert result.status == "settled"
    # Same reconciliation is idempotent; it must not charge the same usage twice.
    await ledger.settle(
        reservation_id,
        actual_cost_rub=Decimal("123.4"),
        input_tokens=100,
        output_tokens=20,
    )
    async with db.connect() as connection:
        day = (
            (await connection.execute(select(budget_days).where(budget_days.c.scope_id == scope)))
            .mappings()
            .one()
        )
        call = (
            (
                await connection.execute(
                    select(model_calls).where(
                        model_calls.c.reservation_id == reservation_id,
                    )
                )
            )
            .mappings()
            .one()
        )
    assert day["reserved_rub"] == 0 and day["spent_rub"] == Decimal("123.4")
    assert call["usage_status"] == "provider" and call["outcome"] == "known"


async def test_known_unused_release_and_reservation_idempotency(db: AsyncEngine) -> None:
    scope, reservation_id = str(uuid4()), str(uuid4())
    ledger = BudgetLedger(db)
    for _ in range(2):
        await ledger.reserve(
            reservation_id,
            amount_rub=Decimal("450"),
            tariff=fake_tariff(),
            scope_id=scope,
        )
    with pytest.raises(AppError) as error:
        await ledger.reserve(
            reservation_id,
            amount_rub=Decimal("449"),
            tariff=fake_tariff(),
            scope_id=scope,
        )
    assert error.value.code == ErrorCode.idempotency_conflict
    with pytest.raises(AppError):
        await ledger.release(reservation_id, known_unused=False)
    released = await ledger.release(reservation_id, known_unused=True)
    assert released.status == "released"
    await ledger.release(reservation_id, known_unused=True)
    await ledger.reserve(
        str(uuid4()), amount_rub=Decimal("450"), tariff=fake_tariff(), scope_id=scope
    )


async def test_unverified_tariff_refuses_reserve_without_a_day_row(db: AsyncEngine) -> None:
    scope = str(uuid4())
    tariff = Tariff(model_id="deepseek/deepseek-v4.1-flash", version="unknown")
    with pytest.raises(AppError) as error:
        await BudgetLedger(db).reserve(
            str(uuid4()),
            amount_rub=Decimal("1"),
            tariff=tariff,
            scope_id=scope,
        )
    assert error.value.code == ErrorCode.budget_unknown
    async with db.connect() as connection:
        assert (
            await connection.execute(
                select(budget_days).where(
                    budget_days.c.scope_id == scope,
                )
            )
        ).first() is None


async def test_provider_overrun_is_recorded_truthfully_and_closes_gate(db: AsyncEngine) -> None:
    scope, reservation_id = str(uuid4()), str(uuid4())
    ledger = BudgetLedger(db)
    await ledger.reserve(
        reservation_id, amount_rub=Decimal("400"), tariff=fake_tariff(), scope_id=scope
    )
    await ledger.mark_started(reservation_id, call_id=str(uuid4()))
    await ledger.settle(
        reservation_id,
        actual_cost_rub=Decimal("451"),
        input_tokens=100,
        output_tokens=20,
    )
    with pytest.raises(AppError) as error:
        await ledger.reserve(
            str(uuid4()), amount_rub=Decimal("1"), tariff=fake_tariff(), scope_id=scope
        )
    assert error.value.code == ErrorCode.budget_exceeded
    async with db.connect() as connection:
        actual = (
            await connection.execute(
                select(budget_days.c.spent_rub).where(
                    budget_days.c.scope_id == scope,
                )
            )
        ).scalar_one()
    assert actual == Decimal("451")
