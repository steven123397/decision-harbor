"""Index running query runs for lease-expiry takeover claims."""

from alembic import op


revision = "platform_0005"
down_revision = "platform_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX query_runs_takeover_idx ON query_runs (created_at, id) WHERE status = 'running'")


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_takeover_idx")
