from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import create_engine, text

from app.config import settings

analytics_engine = create_engine(
    settings.analytics_database_url,
    pool_pre_ping=True,
    connect_args={"options": f"-c statement_timeout={settings.query_statement_timeout_ms}"},
)

_PG_TYPE_NAMES = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    700: "real",
    701: "double precision",
    1043: "varchar",
    1082: "date",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
    3802: "jsonb",
}


@dataclass(slots=True)
class ExecutionResult:
    columns: list[dict[str, str]]
    rows: list[list]
    row_count: int
    duration_ms: int


@dataclass(slots=True)
class ExecutionError:
    code: str
    message: str


def execute_query(sql: str) -> ExecutionResult | ExecutionError:
    start = time.perf_counter()
    try:
        with analytics_engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = [
                {"name": col[0], "type": _PG_TYPE_NAMES.get(col[1], str(col[1]))}
                for col in result.cursor.description
            ]
            rows = [list(row) for row in result.cursor.fetchall()]
            duration_ms = int((time.perf_counter() - start) * 1000)
            return ExecutionResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                duration_ms=duration_ms,
            )
    except Exception as e:
        error_msg = str(e)
        if "statement timeout" in error_msg.lower() or "canceling statement" in error_msg.lower():
            return ExecutionError(code="TIMEOUT", message="查询执行超时")
        return ExecutionError(code="EXECUTION_ERROR", message=f"查询执行失败: {error_msg.splitlines()[0]}")
