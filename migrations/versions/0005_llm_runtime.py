"""Pin effective config on durable LLM runs without touching gates or spend."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy runs stay NULL: no guessed config and no reset of held reservations.
    op.execute("ALTER TABLE llm_runs ADD COLUMN runtime_snapshot jsonb")


def downgrade():
    op.execute("ALTER TABLE llm_runs DROP COLUMN runtime_snapshot")
