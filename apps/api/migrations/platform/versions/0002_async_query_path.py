"""Add the durable async queue and result snapshots."""

from alembic import op


revision = "platform_0002"
down_revision = "platform_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_status_check
        CHECK (status IN ('received', 'rejected', 'queued', 'running', 'succeeded', 'failed'))
        """
    )
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_state_facts CHECK (
            (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'queued' AND policy_decision = 'allowed' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'running' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
        )
        """
    )
    op.execute("DROP INDEX query_runs_recovery_idx")
    op.execute(
        "CREATE INDEX query_runs_queue_idx ON query_runs (created_at, id) WHERE status = 'queued'"
    )
    op.execute(
        """
        CREATE TABLE query_results (
            query_run_id uuid PRIMARY KEY REFERENCES query_runs(id) ON DELETE CASCADE,
            columns_json jsonb NOT NULL CHECK (jsonb_typeof(columns_json) = 'array'),
            rows_json jsonb NOT NULL CHECK (jsonb_typeof(rows_json) = 'array'),
            truncated boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("REVOKE ALL ON TABLE query_results FROM PUBLIC")
    op.execute("GRANT SELECT ON TABLE query_results TO platform_app")
    op.execute("GRANT SELECT, UPDATE ON TABLE query_runs TO platform_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE query_results TO platform_worker")


def downgrade() -> None:
    op.execute("DROP TABLE query_results")
    op.execute("DROP INDEX query_runs_queue_idx")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute(
        """
        UPDATE query_runs
        SET status = 'failed',
            error_code = 'execution_interrupted',
            error_summary = 'Execution was interrupted before completion.',
            finished_at = now(),
            duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
        WHERE status = 'queued'
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_status_check
        CHECK (status IN ('received', 'running', 'succeeded', 'rejected', 'failed'))
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_state_facts CHECK (
            (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'running' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX query_runs_recovery_idx ON query_runs (status, created_at) WHERE status IN ('received', 'running')"
    )
