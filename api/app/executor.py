"""只读执行器：以 analytics_reader 身份执行策略批准的语句。

资源限制（超时、行上限、截断语义）与序列化规则见
docs/design/query-governance.md 与 docs/design/query-runs-api.md。
只接受策略输出的归一化语句，不接受原始用户输入。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as time_of_day
from decimal import Decimal
from typing import Any

import psycopg.errors
from psycopg.postgres import types as pg_types
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int


class ExecutionError(Exception):
    """执行失败；error_code 对应错误码注册表。"""

    def __init__(self, error_code: str, message: str, duration_ms: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.duration_ms = duration_ms


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # numeric 序列化为字符串，保持契约要求的定点语义
        return str(value)
    if isinstance(value, (datetime, date, time_of_day)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return str(value)


def _column_type_name(type_oid: int | None) -> str:
    if type_oid is None:
        return "unknown"
    info = pg_types.get(type_oid)
    return info.name if info is not None else "unknown"


def execute_readonly(
    engine: Engine, normalized_sql: str, *, timeout_ms: int, row_limit: int
) -> ExecutionResult:
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            result = conn.exec_driver_sql(normalized_sql)
            description = result.cursor.description or []
            columns = [
                {"name": col.name, "type": _column_type_name(col.type_code)}
                for col in description
            ]
            fetched = result.fetchmany(row_limit + 1)
            truncated = len(fetched) > row_limit
            rows = [
                [_json_value(value) for value in row]
                for row in fetched[:row_limit]
            ]
    except DBAPIError as error:
        duration = elapsed_ms()
        if isinstance(error.orig, psycopg.errors.QueryCanceled):
            raise ExecutionError(
                "execution_timeout",
                f"查询超过语句超时上限 {timeout_ms} ms",
                duration,
            ) from error
        raise ExecutionError(
            "execution_error",
            str(error.orig) if error.orig is not None else str(error),
            duration,
        ) from error

    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=elapsed_ms(),
    )
