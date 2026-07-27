"""Separate readiness metadata from the analytics query identity."""

from alembic import op


revision = "analytics_0002"
down_revision = "analytics_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE ALL ON SCHEMA maintenance FROM analytics_reader")
    op.execute("REVOKE ALL ON TABLE maintenance.dataset_seeds FROM analytics_reader")
    op.execute("REVOKE ALL ON TABLE public.alembic_version FROM analytics_reader")
    op.execute("GRANT USAGE ON SCHEMA maintenance TO analytics_readiness")
    op.execute("GRANT SELECT ON TABLE maintenance.dataset_seeds TO analytics_readiness")
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO analytics_readiness")


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE maintenance.dataset_seeds FROM analytics_readiness")
    op.execute("REVOKE ALL ON TABLE public.alembic_version FROM analytics_readiness")
    op.execute("REVOKE ALL ON SCHEMA maintenance FROM analytics_readiness")
    op.execute("GRANT USAGE ON SCHEMA maintenance TO analytics_reader")
    op.execute("GRANT SELECT ON TABLE maintenance.dataset_seeds TO analytics_reader")
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO analytics_reader")
