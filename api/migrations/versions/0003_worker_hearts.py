"""worker 心跳表：容器健康信号与后续租约续期的共用基础。

Revision ID: 0003_worker_hearts
Revises: 0002_async_lifecycle
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_worker_hearts"
down_revision = "0002_async_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_hearts",
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON worker_hearts TO platform_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON worker_hearts FROM platform_app")
    op.drop_table("worker_hearts")
