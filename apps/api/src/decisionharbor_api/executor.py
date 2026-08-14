from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from time import monotonic
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from .domain import ExecutionFailure, QueryResult
from .settings import Settings


_TYPE_NAMES = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    1042: "char",
    1043: "varchar",
    1082: "date",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
}


class SqlAlchemyAnalyticsExecutor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: Engine = create_engine(
            settings.analytics_runtime_url,
            poolclass=NullPool,
            pool_pre_ping=False,
            connect_args={"connect_timeout": settings.connect_timeout_seconds},
        )
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_queries)

    def dispose(self) -> None:
        self.engine.dispose()

    def execute(self, sql: str) -> QueryResult:
        if not self._slots.acquire(blocking=False):
            raise ExecutionFailure("executor_busy", "The query executor is busy")
        started = monotonic()
        try:
            return self._execute(sql, started)
        finally:
            self._slots.release()

    def _execute(self, sql: str, started: float) -> QueryResult:
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    connection.exec_driver_sql("SET LOCAL search_path TO analytics, pg_catalog")
                    connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{self.settings.statement_timeout_ms}ms'")
                    connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{self.settings.lock_timeout_ms}ms'")
                    result = connection.exec_driver_sql(sql)
                    descriptions = tuple(getattr(result.cursor, "description", ()) or ())
                    if len(descriptions) > self.settings.max_result_columns:
                        raise ExecutionFailure("result_shape_too_large", "Result has too many columns")
                    rows = result.fetchmany(self.settings.max_result_rows + 1)
                    if len(rows) > self.settings.max_result_rows:
                        raise ExecutionFailure("result_limit_exceeded", "Result exceeds the maximum row count")
                    column_types = tuple(_TYPE_NAMES.get(getattr(column, "type_code", None), f"oid:{getattr(column, 'type_code', 'unknown')}") for column in descriptions)
                    normalized_rows = tuple(
                        tuple(_serialize_value(value, column_types[index]) for index, value in enumerate(row))
                        for row in rows
                    )
                    columns = tuple(
                        {"name": column.name, "type": column_types[index]}
                        for index, column in enumerate(descriptions)
                    )
                    payload = {
                        "columns": columns,
                        "rows": normalized_rows,
                        "row_count": len(normalized_rows),
                    }
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
                    if len(encoded) > self.settings.max_result_bytes:
                        raise ExecutionFailure("result_payload_too_large", "Result exceeds the maximum response size")
                    return QueryResult(columns, normalized_rows, len(normalized_rows), int((monotonic() - started) * 1000))
        except ExecutionFailure:
            raise
        except DBAPIError as error:
            code = getattr(getattr(error, "orig", None), "sqlstate", None)
            if code == "57014":
                raise ExecutionFailure("query_timeout", "Query exceeded the statement timeout") from error
            if code == "55P03":
                raise ExecutionFailure("lock_timeout", "Query exceeded the lock timeout") from error
            if isinstance(error, OperationalError):
                raise ExecutionFailure("analytics_unavailable", "Analytics database is unavailable") from error
            raise ExecutionFailure("analytics_execution_error", "Analytics query execution failed") from error
        except SQLAlchemyError as error:
            raise ExecutionFailure("analytics_execution_error", "Analytics query execution failed") from error


def _serialize_value(value: Any, column_type: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        if column_type == "bigint" and isinstance(value, int):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise ExecutionFailure("result_serialization_error", "A result value cannot be serialized")


def _json_default(value: Any) -> Any:
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
