"""Create the platform query audit store."""

from alembic import op


revision = "platform_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE query_runs (
            id uuid PRIMARY KEY,
            raw_sql text NOT NULL,
            status text NOT NULL CHECK (status IN ('received', 'running', 'succeeded', 'rejected', 'failed')),
            policy_decision text NOT NULL CHECK (policy_decision IN ('not_evaluated', 'allowed', 'rejected')),
            policy_version text NOT NULL,
            referenced_objects jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(referenced_objects) = 'array'),
            statement_timeout_ms integer NOT NULL CHECK (statement_timeout_ms > 0 AND statement_timeout_ms <= 30000),
            max_rows integer NOT NULL CHECK (max_rows > 0 AND max_rows <= 5000),
            returned_row_count integer CHECK (returned_row_count >= 0 AND returned_row_count <= max_rows),
            result_truncated boolean,
            error_code varchar(64),
            error_summary varchar(512),
            created_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            duration_ms integer CHECK (duration_ms >= 0),
            CONSTRAINT query_runs_state_facts CHECK (
                (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
                OR (status = 'running' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
                OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
                OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
                OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX query_runs_recovery_idx ON query_runs (status, created_at) WHERE status IN ('received', 'running')"
    )
    op.execute("REVOKE ALL ON TABLE query_runs FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE query_runs TO platform_app")
    op.execute("GRANT SELECT ON TABLE alembic_version TO platform_app")


def downgrade() -> None:
    op.execute("DROP TABLE query_runs")
