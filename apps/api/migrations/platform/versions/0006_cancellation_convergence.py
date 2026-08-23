"""Index cancelling query runs for the takeover/cleanup convergence scan."""

from alembic import op


revision = "platform_0006"
down_revision = "platform_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX query_runs_cancelling_idx ON query_runs (created_at, id) WHERE status = 'cancelling'")


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_cancelling_idx")
