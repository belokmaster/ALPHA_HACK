from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.tables import metadata

sources = Table(
    "news_sources",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("payload", JSONB, nullable=False),
)
items = Table(
    "news_items",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("publisher", String(32), nullable=False),
    Column("canonical_url", String(2048), nullable=False),
    Column("current_document_id", String(128)),
    Column("known_guids", JSONB, nullable=False, server_default="[]"),
    UniqueConstraint("publisher", "canonical_url"),
)
versions = Table(
    "news_versions",
    metadata,
    Column("document_id", ForeignKey("documents.id"), primary_key=True),
    Column("event_id", ForeignKey("events.id"), nullable=False, unique=True),
    Column("source_id", ForeignKey("source_versions.id"), nullable=False),
    Column("item_id", ForeignKey("news_items.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("normalized_sha256", String(64), nullable=False),
    Column("guid", String(1024)),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("item_id", "sequence"),
)
