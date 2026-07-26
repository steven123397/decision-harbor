"""查询执行器：仅以分析只读身份连接 analytics，带语句超时与行数上限。

对应 docs/design/query-governance.md 的资源限制：
- statement_timeout 会话级设置，超时映射 QUERY_TIMEOUT；
- 流式读取上限加一行，超出截断并置 truncated。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.config import get_settings
from app.db import psycopg_url

QUERY_TIMEOUT = "QUERY_TIMEOUT"
EXECUTION_ERROR = "EXECUTION_ERROR"

logger = logging.getLogger("decisionharbor.executor")


@dataclass
class ExecutionResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    truncated: bool
    row_count: int
    duration_ms: int


class ExecutionFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _sanitize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)  # 定点数按精确字符串输出，不转 float
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _type_names(conn: psycopg.Connection, oids: list[int]) -> dict[int, str]:
    """oid → 类型名。oid 来自驱动 description（可信整数），直接内联避免参数类型推断问题。"""
    names: dict[int, str] = {}
    for oid in set(oids):
        try:
            row = conn.execute(f"SELECT {int(oid)}::regtype::text").fetchone()
            names[oid] = row[0] if row else "unknown"
        except psycopg.Error:
            names[oid] = "unknown"
    return names


def execute(sql: str) -> ExecutionResult:
    settings = get_settings()
    start = time.perf_counter()
    try:
        with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
            # 事务级只读：角色默认只读之外的第二道事务防线
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET statement_timeout = {settings.statement_timeout_ms}")
            with conn.cursor() as cur:
                cur.execute(sql)
                description = list(cur.description or [])
                type_names = _type_names(conn, [col.type_code for col in description])
                columns = [
                    {
                        "name": col.name or f"column_{index}",
                        "type": type_names.get(col.type_code, "unknown"),
                    }
                    for index, col in enumerate(description)
                ]
                limit = settings.max_rows
                fetched: list[tuple[Any, ...]] = []
                while len(fetched) <= limit:
                    batch = cur.fetchmany(limit + 1 - len(fetched))
                    if not batch:
                        break
                    fetched.extend(batch)
                truncated = len(fetched) > limit
                rows = fetched[:limit]
    except psycopg.errors.QueryCanceled as exc:
        logger.warning("query cancelled by statement_timeout: %s", exc)
        raise ExecutionFailure(QUERY_TIMEOUT, "执行超过语句超时限制被取消。") from exc
    except psycopg.Error as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        primary = exc.diag.message_primary if exc.diag else None
        # 数据库原文只进服务端日志；对外只给稳定、不含数据的摘要
        logger.warning(
            "query execution failed: sqlstate=%s error=%s sql=%s",
            sqlstate, (primary or str(exc))[:500], sql[:500],
        )
        public = (
            f"数据库执行错误（SQLSTATE {sqlstate}）。"
            if sqlstate
            else "数据库执行错误。"
        )
        raise ExecutionFailure(EXECUTION_ERROR, public) from exc
    duration_ms = int((time.perf_counter() - start) * 1000)
    return ExecutionResult(
        columns=columns,
        rows=[[_sanitize(value) for value in row] for row in rows],
        truncated=truncated,
        row_count=len(rows),
        duration_ms=duration_ms,
    )
