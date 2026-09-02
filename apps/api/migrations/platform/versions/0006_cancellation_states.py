"""Add durable cancellation states to query runs."""

from alembic import op


revision = "platform_0006"
down_revision = "platform_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_status_check
        CHECK (status IN ('received', 'rejected', 'queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed'))
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_state_facts CHECK (
            (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'queued' AND policy_decision = 'allowed' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status IN ('running', 'cancelling') AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'cancelled' AND policy_decision = 'allowed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX query_runs_cancelling_idx ON query_runs (created_at, id) WHERE status = 'cancelling'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_cancelling_idx")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute(
        """
        UPDATE query_runs
        SET status = 'failed',
            error_code = 'execution_interrupted',
            error_summary = 'Execution was interrupted before completion.',
            finished_at = COALESCE(finished_at, now()),
            duration_ms = COALESCE(duration_ms, GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer), 0),
            owner_worker_id = NULL,
            heartbeat_at = NULL,
            lease_expires_at = NULL
        WHERE status IN ('cancelling', 'cancelled')
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_status_check
        CHECK (status IN ('received', 'rejected', 'queued', 'running', 'succeeded', 'failed'))
        """
    )
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
