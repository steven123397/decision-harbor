"""Let the worker delete result snapshots that outlived their retention."""

from alembic import op


revision = "platform_0004"
down_revision = "platform_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON TABLE query_run_results TO platform_worker")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON TABLE query_run_results FROM platform_worker")
