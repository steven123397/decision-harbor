"""系统开关表：运维开关（如 worker 排水）的存放处。

不复用 dataset_markers——ADR-0013 界定该表为 seed 幂等标记，
运维开关重载其语义。

Revision ID: 0004_system_flags
Revises: 0003_worker_hearts
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_system_flags"
down_revision = "0003_worker_hearts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_flags",
        sa.Column("flag", sa.String(128), primary_key=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON system_flags TO platform_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON system_flags FROM platform_app")
    op.drop_table("system_flags")
