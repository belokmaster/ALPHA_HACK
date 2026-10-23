"""Foundation relational storage; payloads are versioned public DTOs."""

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.schema import SchemaItem

from app.budgets.tables import register

metadata = MetaData()


def table(name: str, *columns: SchemaItem, **kw: Any) -> Table:
    return Table(name, metadata, Column("id", String(128), primary_key=True), *columns, **kw)


def payload() -> Column[dict[str, Any]]:
    return Column("payload", JSONB, nullable=False)


principals = table("principals", Column("acl_version", Integer, nullable=False))
source_versions = table(
    "source_versions",
    Column("sha256", String(64), nullable=False),
    Column("import_version", String(40), nullable=False),
    payload(),
    UniqueConstraint("sha256", "import_version"),
)
quality_issues = table(
    "quality_issues", Column("source_id", ForeignKey("source_versions.id")), payload()
)
companies = table(
    "company_snapshots",
    Column("source_id", ForeignKey("source_versions.id"), nullable=False),
    Column("company_id", String(128), unique=True, nullable=False),
    payload(),
)
features = table(
    "features", Column("snapshot_id", ForeignKey("company_snapshots.id"), nullable=False), payload()
)
relations = table(
    "relations",
    Column("source_id", ForeignKey("source_versions.id"), nullable=False),
    Column("kind", String(30), nullable=False),
    payload(),
    CheckConstraint("kind IN ('group','shared_person','observed_payment','self_transfer')"),
)
grants = Table(
    "grants",
    metadata,
    Column("principal_id", ForeignKey("principals.id"), primary_key=True),
    Column("company_id", ForeignKey("company_snapshots.company_id"), primary_key=True),
    payload(),
)
contexts = table(
    "contexts",
    Column("principal_id", ForeignKey("principals.id"), unique=True, nullable=False),
    Column("version", Integer, nullable=False),
    payload(),
    CheckConstraint("version > 0"),
)
sessions = table(
    "sessions",
    Column("principal_id", ForeignKey("principals.id"), nullable=False),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
documents = table(
    "documents",
    Column("source_id", ForeignKey("source_versions.id"), nullable=False),
    Column("company_id", ForeignKey("company_snapshots.company_id"), nullable=True),
    payload(),
)
evidence = table(
    "evidence", Column("document_id", ForeignKey("documents.id"), nullable=False), payload()
)
events = table(
    "events", Column("source_id", ForeignKey("source_versions.id"), nullable=False), payload()
)
jobs = table(
    "jobs",
    Column("principal_id", ForeignKey("principals.id"), nullable=False),
    Column("company_id", ForeignKey("company_snapshots.company_id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("route", String(80), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="0"),
    Column("lease_until", DateTime(timezone=True)),
    Column("lease_token", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("result_id", String(128)),
    Column("error", JSONB),
    payload(),
    UniqueConstraint("principal_id", "route", "idempotency_key"),
    CheckConstraint("status IN ('queued','running','succeeded','partial','failed','cancelled')"),
    CheckConstraint("attempt >= 0"),
)
Index("jobs_claim", jobs.c.status, jobs.c.created_at)
runs = table("runs", Column("job_id", ForeignKey("jobs.id"), nullable=False), payload())
tool_calls = table("tool_calls", Column("run_id", ForeignKey("runs.id"), nullable=False), payload())
analyses = table(
    "analyses",
    Column("job_id", ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("principal_id", ForeignKey("principals.id"), nullable=False),
    Column("company_id", ForeignKey("company_snapshots.company_id"), nullable=False),
    payload(),
)
conversations = table(
    "conversations",
    Column("analysis_id", ForeignKey("analyses.id"), nullable=False),
    Column("principal_id", ForeignKey("principals.id"), nullable=False),
    payload(),
)
messages = table(
    "messages",
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("job_id", ForeignKey("jobs.id"), unique=True, nullable=False),
    payload(),
)
register(metadata)
