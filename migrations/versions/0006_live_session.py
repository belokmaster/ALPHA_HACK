"""Explicit durable live authorization and external accounting, no legacy updates."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    from migrations.schema_0006_sessions import external_expenses, sessions

    sessions.create(op.get_bind())
    external_expenses.create(op.get_bind())
    op.execute("ALTER TABLE llm_runs ADD COLUMN session_id varchar(128)")
    op.execute(
        "ALTER TABLE llm_attempts ADD COLUMN session_id varchar(128), "
        "ADD COLUMN session_reserve numeric(18,6), ADD COLUMN sent_at timestamptz, "
        "ADD COLUMN external_before numeric(24,15), ADD COLUMN external_after numeric(24,15)"
    )


def downgrade():
    for col in ("external_after", "external_before", "sent_at", "session_reserve", "session_id"):
        op.execute("ALTER TABLE llm_attempts DROP COLUMN " + col)
    op.execute("ALTER TABLE llm_runs DROP COLUMN session_id")
    op.drop_table("llm_external_expenses")
    op.drop_table("llm_sessions")
