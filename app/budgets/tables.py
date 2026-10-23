from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

budget_days = Table(
    "budget_days",
    metadata,
    Column("scope_id", String(128), primary_key=True),
    Column("budget_day", Date, primary_key=True),
    Column("reserved_rub", Numeric(18, 6), nullable=False, server_default="0"),
    Column("spent_rub", Numeric(18, 6), nullable=False, server_default="0"),
    Column("internal_cap_rub", Numeric(18, 6), nullable=False),
    Column("provider_cap_rub", Numeric(18, 6), nullable=False),
    CheckConstraint("reserved_rub >= 0 AND spent_rub >= 0", name="budget_nonnegative"),
    CheckConstraint(
        "internal_cap_rub > 0 AND internal_cap_rub <= 450 "
        "AND provider_cap_rub >= internal_cap_rub AND provider_cap_rub <= 500",
        name="budget_cap_bounds",
    ),
)

reservations = Table(
    "reservations",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("scope_id", String(128), nullable=False),
    Column("budget_day", Date, nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("tariff_version", String(128), nullable=False),
    Column("amount_rub", Numeric(18, 6), nullable=False),
    Column("actual_cost_rub", Numeric(18, 6)),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["scope_id", "budget_day"],
        ["budget_days.scope_id", "budget_days.budget_day"],
        name="reservation_budget_day_fk",
    ),
    CheckConstraint("amount_rub > 0", name="reservation_positive"),
    CheckConstraint(
        "actual_cost_rub IS NULL OR actual_cost_rub >= 0", name="reservation_actual_nonnegative"
    ),
    CheckConstraint(
        "status IN ('reserved', 'settled', 'released', 'uncertain')", name="reservation_status"
    ),
)

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("reservation_id", ForeignKey("reservations.id"), nullable=False),
    Column("job_id", String(128), ForeignKey("jobs.id")),
    Column("model_id", String(128), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("usage_status", String(20), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_rub", Numeric(18, 6)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("reservation_id", name="model_call_one_attempt_per_reservation"),
    CheckConstraint("outcome IN ('started', 'known', 'uncertain')", name="model_call_outcome"),
    CheckConstraint("usage_status IN ('provider', 'unknown')", name="model_call_usage_status"),
    CheckConstraint(
        "(input_tokens IS NULL OR input_tokens >= 0) "
        "AND (output_tokens IS NULL OR output_tokens >= 0) "
        "AND (cost_rub IS NULL OR cost_rub >= 0)",
        name="model_call_usage_nonnegative",
    ),
)
Index("model_calls_job_outcome_idx", model_calls.c.job_id, model_calls.c.outcome)


def register(target: MetaData) -> None:
    """Include these tables in the application's migration metadata (which defines jobs)."""
    from app.llm.tables import TABLES

    for table in (budget_days, reservations, model_calls, *TABLES):
        table.to_metadata(target)
