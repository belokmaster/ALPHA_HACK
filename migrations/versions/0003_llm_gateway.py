"""Shared LLM run counters and durable per-key gate. Immutable schema snapshot."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE llm_runs (
        id varchar(128) PRIMARY KEY, scope_id varchar(128) NOT NULL,
        job_id varchar(128) REFERENCES jobs(id), deadline_at timestamptz NOT NULL,
        models_used integer NOT NULL DEFAULT 0, input_used integer NOT NULL DEFAULT 0,
        output_used integer NOT NULL DEFAULT 0, retry_used integer NOT NULL DEFAULT 0,
        repair_used integer NOT NULL DEFAULT 0, quoted_rub numeric(18,6) NOT NULL DEFAULT 0,
        CONSTRAINT llm_run_caps CHECK (
            models_used BETWEEN 0 AND 4 AND input_used BETWEEN 0 AND 24000
            AND output_used BETWEEN 0 AND 4000 AND retry_used BETWEEN 0 AND 1
            AND repair_used BETWEEN 0 AND 1 AND quoted_rub BETWEEN 0 AND 5)
    )""",
    """CREATE TABLE llm_key_gates (
        scope_id varchar(128) PRIMARY KEY, active_call_id varchar(128)
    )""",
    """CREATE TABLE llm_attempts (
        id varchar(128) PRIMARY KEY, run_id varchar(128) NOT NULL REFERENCES llm_runs(id),
        scope_id varchar(128) NOT NULL, model_id varchar(128) NOT NULL,
        tariff_version varchar(128) NOT NULL, input_rate numeric NOT NULL,
        output_rate numeric NOT NULL, input_bound integer NOT NULL,
        output_bound integer NOT NULL, request_sha256 varchar(64) NOT NULL,
        state varchar(20) NOT NULL,
        CONSTRAINT llm_attempt_state CHECK (state IN ('preparing','started','uncertain','closed'))
    )""",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    for name in ("llm_attempts", "llm_key_gates", "llm_runs"):
        op.execute("DROP TABLE " + name)
