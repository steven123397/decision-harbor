"""Grant snapshot deletion for retention cleanup and index expiry lookups."""

from alembic import op


revision = "platform_0003"
down_revision = "platform_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON TABLE result_snapshots TO platform_worker")
    # 谓词与清理查询一致（finished_at IS NOT NULL），使部分索引可被 DELETE 子查询使用。
    op.execute(
        "CREATE INDEX query_runs_finished_at_idx ON query_runs (finished_at) "
        "WHERE finished_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_finished_at_idx")
    op.execute("REVOKE DELETE ON TABLE result_snapshots FROM platform_worker")
