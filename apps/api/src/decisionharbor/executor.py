from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from decisionharbor.domain import JsonCell, QueryColumn
from decisionharbor.snapshots import BuiltSnapshot, SnapshotBuilder, SnapshotTooLarge


TYPE_NAMES = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    1042: "character",
    1043: "character varying",
    1082: "date",
    1114: "timestamp without time zone",
    1184: "timestamp with time zone",
    1700: "numeric",
}


class ExecutionFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ResultColumnDescription(Protocol):
    """psycopg 游标描述列的最小接口。"""

    name: str
    type_code: int


def _unsupported_type_failure() -> ExecutionFailure:
    return ExecutionFailure(
        "unsupported_result_type",
        "The query returned a result type that is not supported.",
    )


def serialize_cell(value: object, type_oid: int) -> JsonCell:
    if type_oid not in TYPE_NAMES:
        raise _unsupported_type_failure()
    if value is None:
        return None
    if type_oid in {20, 1700}:
        if not isinstance(value, (int, Decimal)):
            raise _unsupported_type_failure()
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (bool, int, str)):
        return value
    raise _unsupported_type_failure()


def extract_snapshot(
    fetch_row: Callable[[], Sequence[object] | None],
    description: Sequence[ResultColumnDescription],
    max_rows: int,
) -> BuiltSnapshot:
    """逐行提取并按快照预算累计；预算耗尽即停止读取，不先完整物化再截断。"""
    columns: list[QueryColumn] = []
    for column in description:
        type_name = TYPE_NAMES.get(column.type_code)
        if type_name is None:
            raise _unsupported_type_failure()
        columns.append(QueryColumn(name=column.name, type=type_name))
    type_codes = [column.type_code for column in description]
    builder = SnapshotBuilder(tuple(columns), max_rows=max_rows)
    while True:
        row = fetch_row()
        if row is None:
            break
        serialized = tuple(
            serialize_cell(value, type_codes[index]) for index, value in enumerate(row)
        )
        if not builder.add_row(serialized):
            break
    return builder.build()


class PostgresQueryExecutor:
    def __init__(self, database_url: str, max_concurrency: int) -> None:
        self._engine: Engine = create_engine(
            database_url,
            pool_size=max_concurrency,
            max_overflow=0,
            pool_pre_ping=True,
        )

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> BuiltSnapshot:
        try:
            with self._engine.connect() as connection:
                with connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    connection.exec_driver_sql(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{statement_timeout_ms}ms",),
                    )
                    driver_connection = connection.connection.driver_connection
                    with driver_connection.cursor(name=f"query_{uuid4().hex}") as cursor:
                        cursor.execute(raw_sql)
                        # 逐行读取：内存上限为单行大小加 1 MiB 快照预算。
                        return extract_snapshot(cursor.fetchone, cursor.description or (), max_rows)
        except ExecutionFailure:
            raise
        except SnapshotTooLarge:
            raise ExecutionFailure(
                "result_too_large",
                "The query result exceeds the supported size limit.",
            ) from None
        except psycopg.Error as exc:
            raise map_database_error(exc) from exc
        except DBAPIError as exc:
            if isinstance(exc.orig, psycopg.Error):
                raise map_database_error(exc.orig) from exc
            raise ExecutionFailure("analytics_unavailable", "The analytics database is unavailable.") from exc
        except Exception as exc:
            raise ExecutionFailure("internal_error", "The query could not be completed.") from exc


def map_database_error(error: psycopg.Error) -> ExecutionFailure:
    sqlstate = error.sqlstate or ""
    if sqlstate == "57014":
        return ExecutionFailure("query_timeout", "The query exceeded its time limit.")
    if sqlstate.startswith(("22", "42")):
        return ExecutionFailure(
            "query_semantic_error",
            "The query is not valid for this dataset.",
        )
    if (
        isinstance(error, psycopg.OperationalError)
        or sqlstate.startswith("08")
        or sqlstate in {"57P01", "57P02", "57P03"}
    ):
        return ExecutionFailure("analytics_unavailable", "The analytics database is unavailable.")
    return ExecutionFailure("internal_error", "The query could not be completed.")
