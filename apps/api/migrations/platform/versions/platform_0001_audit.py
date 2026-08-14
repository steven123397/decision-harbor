"""Create platform query audit tables."""

from alembic import op
import sqlalchemy as sa


revision = "platform_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS platform")
    op.create_table(
        "query_runs",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("raw_sql", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("policy_decision", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="policy-v1"),
        sa.Column("policy_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("execution_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('received', 'executing', 'succeeded', 'rejected', 'failed')",
            name="query_runs_state_check",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'rejected', 'failed')",
            name="query_runs_outcome_check",
        ),
        sa.CheckConstraint(
            "policy_decision IN ('not_evaluated', 'allowed', 'rejected')",
            name="query_runs_policy_decision_check",
        ),
        schema="platform",
    )
    op.create_table(
        "query_run_events",
        sa.Column("run_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["platform.query_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
        schema="platform",
    )
    op.create_index("query_runs_created_at_idx", "query_runs", ["created_at"], schema="platform")
    op.execute("GRANT USAGE ON SCHEMA platform TO platform_writer, platform_readiness")
    op.execute("GRANT SELECT, INSERT, UPDATE ON platform.query_runs, platform.query_run_events TO platform_writer")
    op.execute("GRANT SELECT ON platform.alembic_version TO platform_readiness")


def downgrade() -> None:
    op.execute("REVOKE ALL ON platform.query_runs, platform.query_run_events FROM platform_writer")
    op.execute("REVOKE ALL ON platform.alembic_version FROM platform_readiness")
    op.drop_index("query_runs_created_at_idx", table_name="query_runs", schema="platform")
    op.drop_table("query_run_events", schema="platform")
    op.drop_table("query_runs", schema="platform")
