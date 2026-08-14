"""platform 基础结构：query_runs 审计表与 dataset_markers seed 标记。

Revision ID: 0001_platform_baseline
Revises:
Create Date: 2026-08-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_platform_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_runs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'rejected', 'failed')",
            name="ck_query_runs_state",
        ),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("rejection_code", sa.String(64)),
        sa.Column("rejection_message", sa.Text()),
        sa.Column("row_count", sa.Integer()),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "dataset_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marker", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # 迁移由 platform_owner 执行；把 DML 权限授予运行时身份。
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON query_runs TO platform_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON dataset_markers TO platform_app")
    op.execute("GRANT USAGE ON SEQUENCE query_runs_id_seq TO platform_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON query_runs FROM platform_app")
    op.execute("REVOKE ALL ON dataset_markers FROM platform_app")
    op.drop_table("dataset_markers")
    op.drop_table("query_runs")
