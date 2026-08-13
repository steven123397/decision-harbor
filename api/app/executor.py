from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.errors import ExecutionFailed, summarize_error


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    row_count: int


class QueryExecutor:
    def __init__(self, engine: Engine, timeout_ms: int, max_rows: int) -> None:
        self._engine = engine
        self._timeout_ms = timeout_ms
        self._max_rows = max_rows

    def execute(self, sql: str) -> ExecutionResult:
        try:
            with self._engine.connect() as conn:
                conn.execute(text(f"SET statement_timeout = {int(self._timeout_ms)}"))
                result = conn.exec_driver_sql(sql)
                columns = [
                    {"name": key, "type": _column_type_name(result.cursor, index)}
                    for index, key in enumerate(result.keys())
                ]
                fetched = result.fetchmany(self._max_rows + 1)
        except DBAPIError as exc:
            raise _map_dbapi_error(exc) from exc

        if len(fetched) > self._max_rows:
            raise ExecutionFailed(
                "RESULT_LIMIT_EXCEEDED",
                f"Query returned more than {self._max_rows} rows.",
            )

        rows = [[_json_cell(value) for value in row] for row in fetched]
        return ExecutionResult(columns=columns, rows=rows, row_count=len(rows))


_PG_TYPE_NAMES = {
    16: "bool",
    20: "int8",
    21: "int2",
    23: "int4",
    25: "text",
    700: "float4",
    701: "float8",
    1042: "bpchar",
    1043: "varchar",
    1082: "date",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
}


def _column_type_name(cursor: Any, index: int) -> str:
    if cursor is None or not getattr(cursor, "description", None):
        return "unknown"
    description = cursor.description[index]
    type_code = getattr(description, "type_code", None)
    named = getattr(type_code, "name", None)
    if named:
        return str(named)
    if isinstance(type_code, int) and type_code in _PG_TYPE_NAMES:
        return _PG_TYPE_NAMES[type_code]
    return str(type_code or "unknown")


def _json_cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return str(value)


def _map_dbapi_error(exc: DBAPIError) -> ExecutionFailed:
    orig = exc.orig
    pgcode = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if pgcode == "57014":
        return ExecutionFailed("EXECUTION_TIMEOUT", "Query exceeded the statement timeout.")
    return ExecutionFailed("EXECUTION_ERROR", summarize_error(orig or exc))
