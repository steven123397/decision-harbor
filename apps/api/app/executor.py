"""Execute approved SQL with analytics read-only identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError


@dataclass(frozen=True, slots=True)
class ColumnDef:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class ExecutionSuccess:
    columns: list[ColumnDef]
    rows: list[list[Any]]
    row_count: int


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    error_code: str
    error_message: str


class QueryExecutor:
    def __init__(
        self,
        engine: Engine,
        *,
        statement_timeout_ms: int = 5000,
        max_result_rows: int = 1000,
    ) -> None:
        self._engine = engine
        self._statement_timeout_ms = statement_timeout_ms
        self._max_result_rows = max_result_rows

    def execute(self, sql: str) -> ExecutionSuccess | ExecutionFailure:
        try:
            with self._engine.connect() as conn:
                # Force read-only + timeout at DB level for this transaction.
                # statement_timeout cannot be a bound parameter in PostgreSQL SET.
                timeout_ms = max(1, int(self._statement_timeout_ms))
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))
                result = conn.execute(text(sql))
                if result.returns_rows is False:
                    return ExecutionFailure(
                        error_code="EXECUTION_DATABASE_ERROR",
                        error_message="Query did not return rows",
                    )

                columns = [
                    ColumnDef(name=col, type="unknown") for col in result.keys()
                ]
                rows: list[list[Any]] = []
                for i, mapping in enumerate(result.mappings()):
                    if i >= self._max_result_rows:
                        return ExecutionFailure(
                            error_code="EXECUTION_ROW_LIMIT",
                            error_message=(
                                f"Result exceeds maximum of {self._max_result_rows} rows"
                            ),
                        )
                    rows.append([_jsonable(mapping[c.name]) for c in columns])

                return ExecutionSuccess(
                    columns=columns, rows=rows, row_count=len(rows)
                )
        except OperationalError as exc:
            msg = _short_error(exc)
            if "statement timeout" in msg.lower() or "canceling statement" in msg.lower():
                return ExecutionFailure(
                    error_code="EXECUTION_TIMEOUT",
                    error_message="Query exceeded statement timeout",
                )
            return ExecutionFailure(
                error_code="EXECUTION_DATABASE_ERROR",
                error_message=msg,
            )
        except (ProgrammingError, DBAPIError) as exc:
            return ExecutionFailure(
                error_code="EXECUTION_DATABASE_ERROR",
                error_message=_short_error(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return ExecutionFailure(
                error_code="EXECUTION_DATABASE_ERROR",
                error_message=_short_error(exc),
            )


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _short_error(exc: BaseException, limit: int = 500) -> str:
    text_value = str(exc.orig if hasattr(exc, "orig") and exc.orig else exc)
    text_value = text_value.replace("\n", " ").strip()
    if len(text_value) > limit:
        return text_value[: limit - 3] + "..."
    return text_value


def build_readonly_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)
