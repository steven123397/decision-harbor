"""Read-only SQL executor with statement timeout and row limit."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time
from decimal import Decimal

from sqlalchemy import create_engine, text

EXEC_DB_ERROR = "EXEC_DB_ERROR"
EXEC_TIMEOUT = "EXEC_TIMEOUT"
EXEC_ROW_LIMIT_EXCEEDED = "EXEC_ROW_LIMIT_EXCEEDED"
EXEC_INTERRUPTED = "EXEC_INTERRUPTED"
EXEC_INTERNAL = "EXEC_INTERNAL"

_TYPE_BY_OID = {
    20: "bigint",
    21: "smallint",
    23: "integer",
    700: "real",
    701: "double precision",
    1042: "bpchar",
    1043: "varchar",
    16: "boolean",
    18: "char",
    25: "text",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
}


class ExecutionError(Exception):
    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary
        super().__init__(summary)


@dataclass(frozen=True)
class Column:
    name: str
    type: str


@dataclass
class ExecResult:
    columns: list[Column]
    rows: list[list]
    row_count: int
    duration_ms: int


def _sqlstate(exc: Exception) -> str | None:
    state = getattr(exc, "sqlstate", None)
    if state is None:
        orig = getattr(exc, "orig", None)
        state = getattr(orig, "sqlstate", None)
    return state


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(value)
    return str(value)


def _column_type(type_code) -> str:
    return _TYPE_BY_OID.get(type_code, f"oid_{type_code}")


def execute(dsn: str, sql: str, timeout_ms: int, row_limit: int) -> ExecResult:
    engine = create_engine(dsn, isolation_level="AUTOCOMMIT", future=True)
    start = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET statement_timeout = {int(timeout_ms)}"))
            result = conn.execute(text(sql))
            names = list(result.keys())
            cursor = getattr(result, "cursor", None)
            description = getattr(cursor, "description", None) or []
            columns = []
            for index, name in enumerate(names):
                if index < len(description):
                    columns.append(Column(name=name, type=_column_type(description[index].type_code)))
                else:
                    columns.append(Column(name=name, type="unknown"))
            rows = result.fetchmany(row_limit + 1)
            if len(rows) > row_limit:
                raise ExecutionError(
                    EXEC_ROW_LIMIT_EXCEEDED, f"Result exceeds the row limit of {row_limit}"
                )
            data = [[_jsonable(value) for value in row] for row in rows]
    except ExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _sqlstate(exc) == "57014":
            raise ExecutionError(EXEC_TIMEOUT, f"Statement timed out after {int(timeout_ms)} ms") from exc
        raise ExecutionError(EXEC_DB_ERROR, str(exc).strip()) from exc
    finally:
        engine.dispose()

    duration_ms = int((time.monotonic() - start) * 1000)
    return ExecResult(columns=columns, rows=data, row_count=len(data), duration_ms=duration_ms)
