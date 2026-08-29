"""Link retry runs to their source query run."""

from alembic import op


revision = "platform_0007"
down_revision = "platform_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_runs ADD COLUMN retry_of uuid REFERENCES query_runs (id)")
    op.execute("CREATE INDEX query_runs_retry_of_idx ON query_runs (retry_of)")


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_retry_of_idx")
    op.execute("ALTER TABLE query_runs DROP COLUMN retry_of")
