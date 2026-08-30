from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from decisionharbor.domain import (
    RESULT_MAX_ROWS,
    JsonCell,
    QueryColumn,
    QueryResult,
    ResultSnapshotBuilder,
    ResultTooLarge,
)


# Rows are taken from the server-side cursor in batches, so extraction stops as
# soon as the snapshot prefix is settled instead of pulling the whole result in.
FETCH_BATCH_SIZE = 64

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


def serialize_cell(value: object, type_oid: int) -> JsonCell:
    if type_oid not in TYPE_NAMES:
        raise ExecutionFailure(
            "unsupported_result_type",
            "The query returned a result type that is not supported.",
        )
    if value is None:
        return None
    if type_oid in {20, 1700}:
        if not isinstance(value, (int, Decimal)):
            raise ExecutionFailure("unsupported_result_type", "The query returned a result type that is not supported.")
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (bool, int, str)):
        return value
    raise ExecutionFailure(
        "unsupported_result_type",
        "The query returned a result type that is not supported.",
    )


class PostgresQueryExecutor:
    def __init__(self, database_url: str, max_concurrency: int) -> None:
        self._engine: Engine = create_engine(
            database_url,
            pool_size=max_concurrency,
            max_overflow=0,
            pool_pre_ping=True,
        )

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
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
                        description = cursor.description or ()
                        for column in description:
                            if column.type_code not in TYPE_NAMES:
                                raise ExecutionFailure(
                                    "unsupported_result_type",
                                    "The query returned a result type that is not supported.",
                                )
                        columns = tuple(
                            QueryColumn(name=column.name, type=TYPE_NAMES[column.type_code])
                            for column in description
                        )
                        # A run may ask for more rows than a snapshot can hold;
                        # reading the surplus would only be thrown away.
                        row_limit = min(max_rows, RESULT_MAX_ROWS)
                        builder = ResultSnapshotBuilder(columns, max_rows=row_limit)
                        while True:
                            # One row beyond the row bound is still read, because
                            # only an existing row can settle `truncated`.
                            wanted = min(FETCH_BATCH_SIZE, row_limit - builder.row_count + 1)
                            batch = cursor.fetchmany(wanted)
                            if not batch:
                                break
                            for raw_row in batch:
                                row = tuple(
                                    serialize_cell(value, description[index].type_code)
                                    for index, value in enumerate(raw_row)
                                )
                                if not builder.add(row):
                                    break
                            if builder.truncated or len(batch) < wanted:
                                break
                        return builder.build()
        except ExecutionFailure:
            raise
        except ResultTooLarge as exc:
            raise ExecutionFailure(
                "result_too_large", "The query result is too large to store."
            ) from exc
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
