"""Extend the platform store for asynchronous query runs."""

from alembic import op


revision = "platform_0002"
down_revision = "platform_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check",
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_status_check CHECK (
            status IN ('received', 'rejected', 'queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled')
        )
        """
    )
    op.execute(
        """
        UPDATE query_runs
        SET status = 'failed',
            error_code = 'execution_interrupted',
            error_summary = 'Execution was interrupted before completion.',
            finished_at = now(),
            duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
        WHERE status = 'running'
        """
    )
    op.execute(
        """
        CREATE TABLE execution_attempts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES query_runs (id),
            generation integer NOT NULL CHECK (generation > 0),
            worker_id text NOT NULL,
            claimed_at timestamptz NOT NULL,
            lease_expires_at timestamptz NOT NULL,
            finished_at timestamptz,
            CONSTRAINT execution_attempt_lease CHECK (lease_expires_at > claimed_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX execution_attempts_valid_ownership_idx ON execution_attempts (lease_expires_at) WHERE finished_at IS NULL"
    )
    op.execute("CREATE INDEX execution_attempts_run_idx ON execution_attempts (run_id, generation)")
    op.execute("ALTER TABLE query_runs ADD COLUMN current_attempt_id bigint REFERENCES execution_attempts (id)")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_state_facts CHECK (
            (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL AND current_attempt_id IS NULL)
            OR (status = 'queued' AND policy_decision = 'allowed' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL AND current_attempt_id IS NULL)
            OR (status = 'running' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL AND current_attempt_id IS NOT NULL)
            OR (status = 'cancelling' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'cancelled' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
        )
        """
    )
    op.execute("DROP INDEX query_runs_recovery_idx")
    op.execute("CREATE INDEX query_runs_queue_idx ON query_runs (created_at, id) WHERE status = 'queued'")
    op.execute(
        """
        CREATE TABLE result_snapshots (
            run_id uuid PRIMARY KEY REFERENCES query_runs (id),
            payload text NOT NULL,
            truncated boolean NOT NULL,
            row_count integer NOT NULL CHECK (row_count >= 0 AND row_count <= 500),
            byte_size integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 1048576),
            created_at timestamptz NOT NULL
        )
        """
    )
    op.execute("REVOKE ALL ON TABLE execution_attempts FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE execution_attempts TO platform_worker")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE execution_attempts_id_seq TO platform_worker")
    op.execute("REVOKE ALL ON TABLE result_snapshots FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON TABLE result_snapshots TO platform_worker")
    op.execute("GRANT SELECT ON TABLE result_snapshots TO platform_app")
    op.execute("GRANT SELECT, UPDATE ON TABLE query_runs TO platform_worker")
    op.execute("GRANT SELECT ON TABLE alembic_version TO platform_worker")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE execution_attempts, result_snapshots FROM platform_worker")
    op.execute("REVOKE ALL ON TABLE result_snapshots FROM platform_app")
    op.execute("REVOKE SELECT, UPDATE ON TABLE query_runs FROM platform_worker")
    op.execute("REVOKE SELECT ON TABLE alembic_version FROM platform_worker")
    op.execute("DROP TABLE result_snapshots")
    op.execute("CREATE INDEX query_runs_recovery_idx ON query_runs (status, created_at) WHERE status IN ('received', 'running')")
    op.execute("DROP INDEX query_runs_queue_idx")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute("ALTER TABLE query_runs DROP COLUMN current_attempt_id")
    op.execute("DROP INDEX execution_attempts_run_idx")
    op.execute("DROP INDEX execution_attempts_valid_ownership_idx")
    op.execute("DROP TABLE execution_attempts")
    op.execute(
        """
        UPDATE query_runs
        SET status = 'failed',
            error_code = 'execution_interrupted',
            error_summary = 'Execution was interrupted before completion.',
            finished_at = now(),
            duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
        WHERE status IN ('queued', 'running', 'cancelling', 'cancelled')
        """
    )
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_status_check CHECK (
            status IN ('received', 'running', 'succeeded', 'rejected', 'failed')
        )
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_state_facts CHECK (
            (status = 'received' AND policy_decision = 'not_evaluated' AND started_at IS NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'running' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL)
            OR (status = 'succeeded' AND policy_decision = 'allowed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL)
            OR (status = 'rejected' AND policy_decision = 'rejected' AND started_at IS NULL AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
            OR (status = 'failed' AND finished_at IS NOT NULL AND returned_row_count IS NULL AND result_truncated IS NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL)
        )
        """
    )
