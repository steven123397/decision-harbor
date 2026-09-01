"""Allow workers to delete expired result snapshots."""

from alembic import op


revision = "platform_0005"
down_revision = "platform_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT (query_run_id), DELETE ON TABLE query_results TO platform_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT (query_run_id), DELETE ON TABLE query_results FROM platform_worker")
