"""查询运行异步生命周期：八状态、attempt/retry/幂等、租约与快照存储。

expand 阶段：只加结构不改行为；v0.1.0 同步链路继续在原列上运行。

Revision ID: 0002_async_lifecycle
Revises: 0001_platform_baseline
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_async_lifecycle"
down_revision = "0001_platform_baseline"
branch_labels = None
depends_on = None

# received（已受理未入队）与 cancelling（取消已受理未生效）是仅在异步
# 链路中出现的新状态；v0.1.0 四状态保持不动。
ASYNC_STATE_CHECK = (
    "state IN ('received', 'queued', 'running', 'cancelling', 'cancelled', "
    "'succeeded', 'failed', 'rejected')"
)


def upgrade() -> None:
    op.drop_constraint("ck_query_runs_state", "query_runs", type_="check")
    op.create_check_constraint(
        "ck_query_runs_state", "query_runs", ASYNC_STATE_CHECK
    )

    op.add_column(
        "query_runs",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("query_runs", sa.Column("idempotency_key", sa.String(128)))
    op.add_column(
        "query_runs",
        sa.Column("retry_of", sa.BigInteger(), sa.ForeignKey("query_runs.id")),
    )
    op.add_column("query_runs", sa.Column("queued_at", sa.DateTime(timezone=True)))
    op.add_column("query_runs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("query_runs", sa.Column("worker_id", sa.String(128)))
    op.add_column("query_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "query_runs",
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # 幂等键全局唯一（NULL 互不冲突）；队列认领、接管扫描、历史分页与
    # 重试链各走自己的索引。
    op.create_index(
        "uq_query_runs_idempotency_key",
        "query_runs",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_query_runs_state_queued",
        "query_runs",
        ["id"],
        postgresql_where=sa.text("state = 'queued'"),
    )
    op.create_index(
        "ix_query_runs_lease_expires_at",
        "query_runs",
        ["lease_expires_at"],
        postgresql_where=sa.text("lease_expires_at IS NOT NULL"),
    )
    op.create_index("ix_query_runs_created_at", "query_runs", ["created_at", "id"])
    op.create_index(
        "ix_query_runs_retry_of",
        "query_runs",
        ["retry_of"],
        postgresql_where=sa.text("retry_of IS NOT NULL"),
    )

    # 结果快照：一行一运行，与终态发布同事务写入（#8），过期清理整行
    # 删除而审计行保留（#14）。
    op.create_table(
        "query_run_snapshots",
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("query_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON query_run_snapshots TO platform_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON query_run_snapshots FROM platform_app")
    op.drop_table("query_run_snapshots")
    op.drop_index("ix_query_runs_retry_of", "query_runs")
    op.drop_index("ix_query_runs_created_at", "query_runs")
    op.drop_index("ix_query_runs_lease_expires_at", "query_runs")
    op.drop_index("ix_query_runs_state_queued", "query_runs")
    op.drop_index("uq_query_runs_idempotency_key", "query_runs")
    op.drop_column("query_runs", "generation")
    op.drop_column("query_runs", "lease_expires_at")
    op.drop_column("query_runs", "worker_id")
    op.drop_column("query_runs", "started_at")
    op.drop_column("query_runs", "queued_at")
    op.drop_column("query_runs", "retry_of")
    op.drop_column("query_runs", "idempotency_key")
    op.drop_column("query_runs", "attempt")
    op.drop_constraint("ck_query_runs_state", "query_runs", type_="check")
    op.create_check_constraint(
        "ck_query_runs_state",
        "query_runs",
        "state IN ('running', 'succeeded', 'rejected', 'failed')",
    )
