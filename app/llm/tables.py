"""Durable run limits and a fail-closed gate per provider key; no prompts or secrets."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.budgets.tables import metadata

llm_runs = Table(
    "llm_runs",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("scope_id", String(128), nullable=False),
    Column("job_id", String(128), ForeignKey("jobs.id")),
    Column("runtime_snapshot", JSONB, nullable=True),
    Column("session_id", String(128), nullable=True),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("models_used", Integer, nullable=False, server_default="0"),
    Column("input_used", Integer, nullable=False, server_default="0"),
    Column("output_used", Integer, nullable=False, server_default="0"),
    Column("retry_used", Integer, nullable=False, server_default="0"),
    Column("repair_used", Integer, nullable=False, server_default="0"),
    Column("quoted_rub", Numeric(18, 6), nullable=False, server_default="0"),
    CheckConstraint(
        "models_used BETWEEN 0 AND 4 AND input_used BETWEEN 0 AND 24000 "
        "AND output_used BETWEEN 0 AND 4000 AND retry_used BETWEEN 0 AND 1 "
        "AND repair_used BETWEEN 0 AND 1 AND quoted_rub BETWEEN 0 AND 5",
        name="llm_run_caps",
    ),
)
key_gates = Table(
    "llm_key_gates",
    metadata,
    Column("scope_id", String(128), primary_key=True),
    Column("active_call_id", String(128)),
)
attempts = Table(
    "llm_attempts",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("run_id", String(128), ForeignKey("llm_runs.id"), nullable=False),
    Column("scope_id", String(128), nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("tariff_version", String(128), nullable=False),
    Column("input_rate", Numeric(), nullable=False),
    Column("output_rate", Numeric(), nullable=False),
    Column("input_bound", Integer, nullable=False),
    Column("output_bound", Integer, nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("state", String(20), nullable=False),
    Column("session_id", String(128), nullable=True),
    Column("session_reserve", Numeric(18, 6), nullable=True),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    Column("external_before", Numeric(24, 15), nullable=True),
    Column("external_after", Numeric(24, 15), nullable=True),
    CheckConstraint(
        "state IN ('preparing','started','uncertain','closed')", name="llm_attempt_state"
    ),
)
sessions = Table(
    "llm_sessions",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("scope_id", String(128), nullable=False),
    Column("budget_day", Date, nullable=False),
    Column("base_url", String(256), nullable=False),
    Column("state", String(20), nullable=False),
    Column("max_calls", Integer, nullable=False),
    Column("calls_allocated", Integer, nullable=False, server_default="0"),
    Column("calls_sent", Integer, nullable=False, server_default="0"),
    Column("budget_rub", Numeric(18, 6), nullable=False),
    Column("spent_rub", Numeric(18, 6), nullable=False, server_default="0"),
    Column("reserved_rub", Numeric(18, 6), nullable=False, server_default="0"),
    Column("baseline_spend", Numeric(24, 15), nullable=False),
    Column("last_spend", Numeric(24, 15), nullable=False),
    Column("risk_acceptance", String(256), nullable=False),
    Column("authorization_id", String(192), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True)),
    CheckConstraint("state IN ('open','blocked','closed')", name="llm_session_state"),
    CheckConstraint(
        "max_calls BETWEEN 1 AND 30 AND calls_allocated BETWEEN 0 AND max_calls "
        "AND calls_sent BETWEEN 0 AND calls_allocated AND budget_rub > 5 "
        "AND budget_rub <= 50 AND reserved_rub >= 0 AND spent_rub >= 0",
        name="llm_session_caps",
    ),
    UniqueConstraint("scope_id", "authorization_id"),
)

Index("llm_sessions_one_active_key", sessions.c.scope_id, unique=True,
      postgresql_where=sessions.c.state.in_(["open", "blocked"]))

external_expenses = Table(
    "llm_external_expenses",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("scope_id", String(128), nullable=False),
    Column("budget_day", Date, nullable=False),
    Column("amount_rub", Numeric(24, 15), nullable=False),
    Column("reason", String(256), nullable=False),
    CheckConstraint("amount_rub >= 0", name="external_expense_nonnegative"),
)

TABLES = (llm_runs, key_gates, attempts, sessions, external_expenses)
