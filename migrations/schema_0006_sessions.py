"""Immutable 0006 session schema; do not import current application metadata."""
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True)),
    CheckConstraint("state IN ('open','blocked','closed')", name="llm_session_state"),
    CheckConstraint(
        "max_calls BETWEEN 1 AND 30 AND calls_allocated BETWEEN 0 AND max_calls "
        "AND calls_sent BETWEEN 0 AND calls_allocated AND budget_rub > 5 "
        "AND budget_rub <= 50 AND reserved_rub >= 0 AND spent_rub >= 0",
        name="llm_session_caps",
    ),
    UniqueConstraint("scope_id", "budget_day"),
    UniqueConstraint("scope_id", "risk_acceptance"),
)

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
