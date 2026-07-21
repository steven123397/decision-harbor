"""只读执行器：以 analytics 只读身份执行允许的 SQL，施加语句超时与行数上限。

行数超限 fail-closed：取 row_limit+1 行，超过即失败，不返回截断结果。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


@dataclass
class ExecutionResult:
    succeeded: bool
    columns: list[dict] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None


_OID_NAMES = {
    16: "bool",
    18: "char",
    20: "int8",
    23: "int4",
    25: "text",
    1043: "varchar",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
}


def _oid_name(oid: object) -> str:
    try:
        return _OID_NAMES.get(int(oid), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _column_types(result) -> list[str]:
    cursor = getattr(result, "cursor", None)
    description = getattr(cursor, "description", None) if cursor is not None else None
    if not description:
        return ["unknown"] * len(list(result.keys()))
    types: list[str] = []
    for d in description:
        oid = getattr(d, "type_code", None)
        if oid is None and isinstance(d, tuple) and len(d) > 1:
            oid = d[1]
        types.append(_oid_name(oid))
    return types


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _truncate(text_: str, limit: int = 300) -> str:
    return text_ if len(text_) <= limit else text_[:limit]


def _classify(err: Exception) -> str:
    if isinstance(err, DBAPIError):
        orig = getattr(err, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None)
        if sqlstate == "57014":
            return "STATEMENT_TIMEOUT"
    return "EXECUTION_ERROR"


def execute(sql: str, engine: Engine, timeout_ms: int, row_limit: int) -> ExecutionResult:
    start = time.monotonic()
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"SET statement_timeout = {int(timeout_ms)}"))
            result = conn.execute(text(sql))
            fetched = result.fetchmany(row_limit + 1)
            duration = int((time.monotonic() - start) * 1000)
            col_keys = list(result.keys())
            col_types = _column_types(result)
            columns = [{"name": n, "type": t} for n, t in zip(col_keys, col_types)]
            if len(fetched) > row_limit:
                return ExecutionResult(
                    False,
                    columns,
                    [],
                    0,
                    "ROW_LIMIT_EXCEEDED",
                    f"result exceeds row limit {row_limit}",
                    duration,
                )
            rows = [[_json_safe(v) for v in row] for row in fetched]
            return ExecutionResult(True, columns, rows, len(rows), None, None, duration)
    except Exception as err:  # noqa: BLE001
        duration = int((time.monotonic() - start) * 1000)
        return ExecutionResult(
            False, [], [], 0, _classify(err), _truncate(str(err)), duration
        )
