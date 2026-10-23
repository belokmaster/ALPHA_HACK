"""Separate explicit authorization identity from risk wording; preserve all counters."""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE llm_sessions ADD COLUMN authorization_id varchar(192)")
    op.execute("UPDATE llm_sessions SET authorization_id = 'legacy:' || id")
    op.execute("ALTER TABLE llm_sessions ALTER COLUMN authorization_id SET NOT NULL")
    op.drop_constraint("llm_sessions_scope_id_risk_acceptance_key", "llm_sessions", type_="unique")
    op.drop_constraint("llm_sessions_scope_id_budget_day_key", "llm_sessions", type_="unique")
    op.create_unique_constraint("llm_sessions_scope_id_authorization_id_key", "llm_sessions", ["scope_id", "authorization_id"])
    op.execute("CREATE UNIQUE INDEX llm_sessions_one_active_key ON llm_sessions(scope_id) WHERE state IN ('open','blocked')")


def downgrade():
    # Never discard later authorizations to force compatibility with the old schema.
    op.execute("""DO $$ BEGIN IF EXISTS (
      SELECT scope_id FROM llm_sessions GROUP BY scope_id HAVING count(*) > 1
    ) OR EXISTS (SELECT 1 FROM llm_sessions WHERE authorization_id <> 'legacy:' || id)
    THEN RAISE EXCEPTION 'Authorization history requires forward migration'; END IF; END $$""")
    op.drop_index("llm_sessions_one_active_key", table_name="llm_sessions")
    op.drop_constraint("llm_sessions_scope_id_authorization_id_key", "llm_sessions", type_="unique")
    op.drop_column("llm_sessions", "authorization_id")
    op.create_unique_constraint("llm_sessions_scope_id_budget_day_key", "llm_sessions", ["scope_id", "budget_day"])
    op.create_unique_constraint("llm_sessions_scope_id_risk_acceptance_key", "llm_sessions", ["scope_id", "risk_acceptance"])
