"""Persist submit idempotency keys scoped to the product instance."""

from alembic import op


revision = "platform_0004"
down_revision = "platform_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE idempotency_keys (
            scope text NOT NULL,
            key text NOT NULL,
            request_fingerprint text NOT NULL,
            run_id uuid NOT NULL REFERENCES query_runs (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (scope, key)
        )
        """
    )
    op.execute("REVOKE ALL ON TABLE idempotency_keys FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON TABLE idempotency_keys TO platform_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE idempotency_keys FROM platform_app")
    op.execute("DROP TABLE idempotency_keys")
