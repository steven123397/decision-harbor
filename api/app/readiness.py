"""/ready 探测：双库身份、五表 SELECT 权限与 seed 标记。"""

from __future__ import annotations

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import Engine


class Readiness:
    def __init__(self, platform_app_engine: Engine, readonly_dsn: str, marker: str, tables: tuple[str, ...]):
        self._platform = platform_app_engine
        self._readonly_dsn = readonly_dsn
        self._marker = marker
        self._tables = tables

    def check(self) -> tuple[bool, str]:
        try:
            with self._platform.connect() as conn:
                conn.execute(text("SELECT 1 FROM query_runs LIMIT 1"))
        except Exception:
            return False, "platform 数据库未就绪"

        try:
            with psycopg.connect(self._readonly_dsn, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    for table in self._tables:
                        cur.execute(f'SELECT 1 FROM analytics."{table}" LIMIT 1')
        except Exception:
            return False, "analytics 只读身份或固定数据未就绪"

        try:
            current = self._read_marker()
        except Exception:
            return False, "seed 标记不可读"
        if current != self._marker:
            return False, f"seed 标记不匹配（当前 {current}）"
        return True, "ok"

    def _read_marker(self) -> str | None:
        with self._platform.connect() as conn:
            return conn.execute(
                text("SELECT marker FROM dataset_markers WHERE id = 1")
            ).scalar()
