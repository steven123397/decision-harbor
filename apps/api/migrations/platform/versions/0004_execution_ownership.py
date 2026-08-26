"""Coordinate globally valid execution ownership."""

from alembic import op


revision = "platform_0004"
down_revision = "platform_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE query_runs
        ADD COLUMN current_generation integer NOT NULL DEFAULT 0 CHECK (current_generation >= 0),
        ADD COLUMN owner_worker_id varchar(128),
        ADD COLUMN heartbeat_at timestamptz,
        ADD COLUMN lease_expires_at timestamptz
        """
    )
    op.execute(
        """
        CREATE TABLE query_execution_attempts (
            query_run_id uuid NOT NULL REFERENCES query_runs(id) ON DELETE CASCADE,
            generation integer NOT NULL CHECK (generation > 0),
            worker_id varchar(128) NOT NULL CHECK (worker_id <> ''),
            claimed_at timestamptz NOT NULL,
            heartbeat_at timestamptz NOT NULL,
            lease_expires_at timestamptz NOT NULL,
            released_at timestamptz,
            release_reason varchar(32),
            PRIMARY KEY (query_run_id, generation),
            CHECK (lease_expires_at > heartbeat_at),
            CHECK ((released_at IS NULL) = (release_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX query_runs_valid_ownership_idx
        ON query_runs (lease_expires_at)
        WHERE status = 'running'
        """
    )
    op.execute("REVOKE ALL ON TABLE query_execution_attempts FROM PUBLIC")
    op.execute("GRANT SELECT ON TABLE query_execution_attempts TO platform_app")
    op.execute("GRANT SELECT, INSERT ON TABLE query_execution_attempts TO platform_worker")
    op.execute(
        """
        GRANT UPDATE (heartbeat_at, lease_expires_at, released_at, release_reason)
        ON TABLE query_execution_attempts TO platform_worker
        """
    )
    op.execute(
        """
        GRANT UPDATE (current_generation, owner_worker_id, heartbeat_at, lease_expires_at)
        ON TABLE query_runs TO platform_worker
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_valid_ownership_idx")
    op.execute("DROP TABLE query_execution_attempts")
    op.execute(
        """
        ALTER TABLE query_runs
        DROP COLUMN lease_expires_at,
        DROP COLUMN heartbeat_at,
        DROP COLUMN owner_worker_id,
        DROP COLUMN current_generation
        """
    )
