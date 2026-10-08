"""Index the execution ownership the worker sweeps for expired leases."""

from alembic import op


revision = "platform_0005"
down_revision = "platform_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The queue poll asks for runs whose lease expired on every cycle, next to
    # the queued runs `query_runs_queue_idx` already serves. Without an index
    # the sweep reads the whole run history, which grows with every audit fact
    # the platform keeps.
    op.execute(
        """
        CREATE INDEX query_runs_lease_idx
        ON query_runs (lease_expires_at)
        WHERE status IN ('running', 'cancelling')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_lease_idx")
