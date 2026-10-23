"""ML pgvector/FTS storage and graph lookups; public DTOs remain v1."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DDL_ENHANCE_EVIDENCE_PGVECTOR = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    "ALTER TABLE evidence ADD COLUMN IF NOT EXISTS embedding vector(384) NULL;",
    """
    ALTER TABLE evidence ADD COLUMN IF NOT EXISTS fts_doc tsvector GENERATED ALWAYS AS (
        to_tsvector('russian', coalesce(payload->>'quote', '')) ||
        to_tsvector('english', coalesce(payload->>'quote', ''))
    ) STORED;
    """,
    "CREATE INDEX IF NOT EXISTS idx_evidence_fts ON evidence USING GIN (fts_doc);",
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_hnsw
    ON evidence USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
    """,
]


def upgrade():
    for statement in DDL_ENHANCE_EVIDENCE_PGVECTOR:
        op.execute(statement)
    op.execute("CREATE INDEX idx_relations_nodes ON relations USING gin ((payload->'nodes'))")
    op.execute(
        "CREATE INDEX idx_relations_membership ON relations "
        "(source_id, kind, (payload->'nodes'->>1))"
    )


def downgrade():
    op.execute("DROP INDEX idx_relations_membership")
    op.execute("DROP INDEX idx_relations_nodes")
    op.execute("ALTER TABLE evidence DROP COLUMN fts_doc")
    op.execute("ALTER TABLE evidence DROP COLUMN embedding")
    # Extension may have other consumers; do not drop it.
