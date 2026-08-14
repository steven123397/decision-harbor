"""只读执行器：psycopg 直连 analytics，流式取数、超时与行数上限。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg import sql as pg_sql

QY_TIMEOUT = "QY_TIMEOUT"
QY_EXECUTION_ERROR = "QY_EXECUTION_ERROR"


class ExecutionFailure(Exception):
    """执行失败，携带稳定错误码与摘要。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[ColumnDef]
    rows: list[list]
    row_count: int
    truncated: bool
    duration_ms: int


def execute_readonly(
    query: str,
    *,
    readonly_dsn: str,
    statement_timeout_ms: int,
    max_rows: int,
) -> ExecutionResult:
    """以只读身份执行一条已通过策略检查的 SQL。

    错误信息在此映射为稳定摘要，不透传数据库原始文本。
    """
    started = time.perf_counter()
    try:
        with psycopg.connect(readonly_dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    pg_sql.SQL("SET statement_timeout = {}").format(
                        pg_sql.Literal(str(statement_timeout_ms))
                    )
                )
                cur.execute(query)
                columns = [
                    ColumnDef(name=col.name, type=_type_name(conn, col.type_code))
                    for col in (cur.description or [])
                ]
                fetched = cur.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                rows = [_convert_row(row) for row in fetched[:max_rows]]
            conn.rollback()
    except psycopg.errors.QueryCanceled as exc:
        raise ExecutionFailure(
            QY_TIMEOUT, "查询执行超时，已被语句超时限制中止"
        ) from exc
    except psycopg.Error as exc:
        raise ExecutionFailure(
            QY_EXECUTION_ERROR, "查询执行失败，请检查列名与表达式"
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=duration_ms,
    )


def _type_name(conn: psycopg.Connection, oid: int) -> str:
    try:
        return conn.adapters.types.get(oid).name or "unknown"
    except Exception:
        return "unknown"


def _convert_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _convert_row(row: tuple) -> list:
    return [_convert_value(v) for v in row]
