"""Public news registry; company-bound source ownership remains enforced by API."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE documents ALTER COLUMN company_id DROP NOT NULL")
    op.execute("""CREATE TABLE news_sources (
        id varchar(32) PRIMARY KEY, payload jsonb NOT NULL)""")
    op.execute("""CREATE TABLE news_items (
        id varchar(64) PRIMARY KEY, publisher varchar(32) NOT NULL,
        canonical_url varchar(2048) NOT NULL, current_document_id varchar(128),
        known_guids jsonb NOT NULL DEFAULT '[]'::jsonb,
        UNIQUE(publisher, canonical_url))""")
    op.execute("""CREATE TABLE news_versions (
        document_id varchar(128) PRIMARY KEY REFERENCES documents(id),
        event_id varchar(128) NOT NULL UNIQUE REFERENCES events(id),
        source_id varchar(128) NOT NULL REFERENCES source_versions(id),
        item_id varchar(64) NOT NULL REFERENCES news_items(id), sequence integer NOT NULL,
        normalized_sha256 varchar(64) NOT NULL, guid varchar(1024), fetched_at timestamptz NOT NULL,
        UNIQUE(item_id, sequence))""")


def downgrade():
    # Refuse to discard imported public news implicitly.
    op.execute("ALTER TABLE documents ALTER COLUMN company_id SET NOT NULL")
    for name in ("news_versions", "news_items", "news_sources"):
        op.execute("DROP TABLE " + name)
