"""结果快照保留期清理：只删除过期结果内容，保留长期审计事实。"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine


RETENTION_HOURS = 24
RETENTION = timedelta(hours=RETENTION_HOURS)


def is_expired(finished_at: datetime, *, now: datetime | None = None) -> bool:
    """读取与清理共用的过期口径：严格越过 finished_at + 保留期才算过期。"""
    current = now if now is not None else datetime.now(timezone.utc)
    return current > finished_at + RETENTION


class ResultRetentionCleaner:
    """幂等清理：重复执行、调度重叠或进程重启后再执行均为无操作。"""

    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(timezone.utc))

    def cleanup_once(self) -> int:
        """删除已超过 finished_at + 保留期的结果快照；返回删除行数。"""
        cutoff = self._now() - RETENTION
        with self._engine.begin() as connection:
            removed = connection.execute(
                text(
                    """
                    DELETE FROM result_snapshots
                    WHERE run_id IN (
                        SELECT id FROM query_runs
                        WHERE finished_at IS NOT NULL
                          AND finished_at < :cutoff
                    )
                    """
                ),
                {"cutoff": cutoff},
            ).rowcount
        return removed
