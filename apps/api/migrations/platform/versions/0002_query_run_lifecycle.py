"""Extend the platform store with the asynchronous query run lifecycle."""

from alembic import op


revision = "platform_0002"
down_revision = "platform_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE query_runs
        SET status = 'failed',
            error_code = 'execution_interrupted',
            error_summary = 'Execution was interrupted before completion.',
            finished_at = now(),
            duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
        WHERE status IN ('received', 'running')
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs
            ADD COLUMN cancellation_requested_at timestamptz,
            ADD COLUMN execution_attempt_count integer NOT NULL DEFAULT 0,
            ADD COLUMN attempt_number integer,
            ADD COLUMN attempt_worker_id varchar(128),
            ADD COLUMN attempt_generation integer,
            ADD COLUMN lease_expires_at timestamptz,
            ADD COLUMN heartbeat_at timestamptz,
            ADD COLUMN retry_of uuid REFERENCES query_runs (id)
        """
    )
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute("DROP INDEX query_runs_recovery_idx")
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_status_check
        CHECK (status IN (
            'received', 'rejected', 'queued', 'running',
            'succeeded', 'failed', 'cancelling', 'cancelled'
        ))
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_state_facts CHECK (
            (
                status = 'received'
                AND policy_decision = 'not_evaluated'
                AND started_at IS NULL AND finished_at IS NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL
                AND execution_attempt_count = 0
                AND attempt_number IS NULL AND attempt_worker_id IS NULL AND attempt_generation IS NULL
                AND lease_expires_at IS NULL AND heartbeat_at IS NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'rejected'
                AND policy_decision = 'rejected'
                AND started_at IS NULL AND finished_at IS NOT NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL
                AND execution_attempt_count = 0
                AND attempt_number IS NULL AND attempt_worker_id IS NULL AND attempt_generation IS NULL
                AND lease_expires_at IS NULL AND heartbeat_at IS NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'queued'
                AND policy_decision = 'allowed'
                AND started_at IS NULL AND finished_at IS NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL
                AND execution_attempt_count = 0
                AND attempt_number IS NULL AND attempt_worker_id IS NULL AND attempt_generation IS NULL
                AND lease_expires_at IS NULL AND heartbeat_at IS NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'running'
                AND policy_decision = 'allowed'
                AND started_at IS NOT NULL AND finished_at IS NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL
                AND execution_attempt_count >= 1
                AND attempt_number IS NOT NULL AND attempt_worker_id IS NOT NULL AND attempt_generation IS NOT NULL
                AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'cancelling'
                AND policy_decision = 'allowed'
                AND started_at IS NOT NULL AND finished_at IS NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NULL
                AND execution_attempt_count >= 1
                AND attempt_number IS NOT NULL AND attempt_worker_id IS NOT NULL AND attempt_generation IS NOT NULL
                AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL
                AND cancellation_requested_at IS NOT NULL
            ) OR (
                status = 'succeeded'
                AND policy_decision = 'allowed'
                AND started_at IS NOT NULL AND finished_at IS NOT NULL
                AND returned_row_count IS NOT NULL AND result_truncated IS NOT NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL
                AND execution_attempt_count >= 1
                AND attempt_number IS NOT NULL AND attempt_generation IS NOT NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'failed'
                AND finished_at IS NOT NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NOT NULL AND error_summary IS NOT NULL AND duration_ms IS NOT NULL
                AND cancellation_requested_at IS NULL
            ) OR (
                status = 'cancelled'
                AND finished_at IS NOT NULL
                AND returned_row_count IS NULL AND result_truncated IS NULL
                AND error_code IS NULL AND error_summary IS NULL AND duration_ms IS NOT NULL
                AND cancellation_requested_at IS NOT NULL
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_attempt_ownership CHECK (
            (
                attempt_number IS NULL AND attempt_worker_id IS NULL AND attempt_generation IS NULL
                AND lease_expires_at IS NULL AND heartbeat_at IS NULL
            ) OR (
                attempt_number >= 1 AND attempt_worker_id IS NOT NULL AND attempt_generation >= 1
                AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_retry_of
        CHECK (retry_of IS NULL OR retry_of <> id)
        """
    )
    op.execute(
        """
        CREATE FUNCTION query_runs_guard_lifecycle() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('rejected', 'succeeded', 'failed', 'cancelled')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'query run % already reached terminal status %',
                    OLD.id, OLD.status USING ERRCODE = '23514';
            END IF;
            IF NEW.execution_attempt_count < OLD.execution_attempt_count THEN
                RAISE EXCEPTION 'query run % cannot reduce its execution attempt count',
                    OLD.id USING ERRCODE = '23514';
            END IF;
            IF NEW.attempt_number IS NOT NULL AND OLD.attempt_number IS NOT NULL
               AND NEW.attempt_number < OLD.attempt_number THEN
                RAISE EXCEPTION 'query run % cannot reuse execution attempt %',
                    OLD.id, NEW.attempt_number USING ERRCODE = '23514';
            END IF;
            IF NEW.attempt_generation IS NOT NULL AND OLD.attempt_generation IS NOT NULL
               AND NEW.attempt_generation < OLD.attempt_generation THEN
                RAISE EXCEPTION 'query run % cannot move back to execution generation %',
                    OLD.id, NEW.attempt_generation USING ERRCODE = '23514';
            END IF;
            IF OLD.cancellation_requested_at IS NOT NULL THEN
                IF NEW.cancellation_requested_at IS NULL THEN
                    RAISE EXCEPTION 'query run % cannot discard its cancellation request',
                        OLD.id USING ERRCODE = '23514';
                END IF;
                IF NEW.attempt_number IS DISTINCT FROM OLD.attempt_number THEN
                    RAISE EXCEPTION 'query run % cannot change execution attempt after a cancellation request',
                        OLD.id USING ERRCODE = '23514';
                END IF;
                IF NEW.execution_attempt_count > OLD.execution_attempt_count THEN
                    RAISE EXCEPTION 'query run % cannot take a new execution attempt after a cancellation request',
                        OLD.id USING ERRCODE = '23514';
                END IF;
            END IF;
            IF OLD.status = 'cancelling' AND NEW.status NOT IN ('cancelling', 'cancelled') THEN
                RAISE EXCEPTION 'query run % can only leave cancelling as cancelled',
                    OLD.id USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER query_runs_lifecycle_guard
        BEFORE UPDATE ON query_runs
        FOR EACH ROW EXECUTE FUNCTION query_runs_guard_lifecycle()
        """
    )
    op.execute(
        """
        CREATE TABLE query_run_idempotency (
            scope text NOT NULL CHECK (
                scope = 'submit'
                OR scope ~ '^retry:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            ),
            idempotency_key text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 128),
            request_fingerprint varchar(64) NOT NULL,
            query_run_id uuid NOT NULL REFERENCES query_runs (id),
            created_at timestamptz NOT NULL,
            PRIMARY KEY (scope, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX query_runs_recovery_idx ON query_runs (created_at) WHERE status = 'received'"
    )
    op.execute(
        "CREATE INDEX query_runs_queue_idx ON query_runs (created_at) WHERE status = 'queued'"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE query_runs TO platform_worker")
    op.execute("GRANT SELECT, INSERT ON TABLE query_run_idempotency TO platform_app")


def downgrade() -> None:
    op.execute("DROP TABLE query_run_idempotency")
    op.execute("DROP TRIGGER query_runs_lifecycle_guard ON query_runs")
    op.execute("DROP FUNCTION query_runs_guard_lifecycle()")
    op.execute("DROP INDEX query_runs_queue_idx")
    op.execute("DROP INDEX query_runs_recovery_idx")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_retry_of")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_attempt_ownership")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_state_facts")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_status_check")
    op.execute(
        """
        ALTER TABLE query_runs
            DROP COLUMN retry_of,
            DROP COLUMN heartbeat_at,
            DROP COLUMN lease_expires_at,
            DROP COLUMN attempt_generation,
            DROP COLUMN attempt_worker_id,
            DROP COLUMN attempt_number,
            DROP COLUMN execution_attempt_count,
            DROP COLUMN cancellation_requested_at
        """
    )
    op.execute(
        """
        ALTER TABLE query_runs ADD CONSTRAINT query_runs_status_check
        CHECK (status IN ('received', 'running', 'succeeded', 'rejected', 'failed'))
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
    op.execute(
        "CREATE INDEX query_runs_recovery_idx ON query_runs (status, created_at) WHERE status IN ('received', 'running')"
    )
