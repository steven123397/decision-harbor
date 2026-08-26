"""Add globally scoped idempotent query submission."""

from alembic import op


revision = "platform_0003"
down_revision = "platform_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_runs ADD COLUMN idempotency_key varchar(128)")
    op.execute(
        """
        ALTER TABLE query_runs
        ADD CONSTRAINT query_runs_idempotency_key_format CHECK (
            idempotency_key IS NULL OR idempotency_key COLLATE "C" ~ '^[!-~]{1,128}$'
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX query_runs_submission_idempotency_key_idx
        ON query_runs (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_submission_idempotency_key_idx")
    op.execute("ALTER TABLE query_runs DROP CONSTRAINT query_runs_idempotency_key_format")
    op.execute("ALTER TABLE query_runs DROP COLUMN idempotency_key")
