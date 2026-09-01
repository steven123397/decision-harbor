"""Index the run history keyset ordering."""

from alembic import op


revision = "platform_0008"
down_revision = "platform_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史分页按 (created_at DESC, id DESC) 键集扫描；索引避免全表排序。
    op.execute(
        "CREATE INDEX query_runs_history_idx ON query_runs (created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX query_runs_history_idx")
