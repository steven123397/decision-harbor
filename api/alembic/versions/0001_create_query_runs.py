"""create query_runs audit table

Revision ID: 0001
Revises:
Create Date: 2026-07-21
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_query_runs_created_at", "query_runs", ["created_at"])
    op.create_index("ix_query_runs_status", "query_runs", ["status"])

    writer = os.environ.get("DH_PLATFORM_WRITER_USER", "dh_platform_writer")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {writer}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON query_runs TO {writer}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE query_runs_id_seq TO {writer}")


def downgrade() -> None:
    op.drop_index("ix_query_runs_status", table_name="query_runs")
    op.drop_index("ix_query_runs_created_at", table_name="query_runs")
    op.drop_table("query_runs")
