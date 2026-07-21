"""create query_runs table

Revision ID: 001
Revises:
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "query_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("raw_sql", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reject_code", sa.String(40), nullable=True),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("columns_json", JSONB, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("query_runs")
