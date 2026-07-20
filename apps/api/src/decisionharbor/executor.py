from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from decisionharbor.dataset import DatasetContract
from decisionharbor.domain import JsonCell, QueryColumn, QueryResult


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
                        fetched_rows = cursor.fetchmany(max_rows + 1)
                        truncated = len(fetched_rows) > max_rows
                        rows = tuple(
                            tuple(serialize_cell(value, description[index].type_code) for index, value in enumerate(row))
                            for row in fetched_rows[:max_rows]
                        )
                        columns = tuple(
                            QueryColumn(name=column.name, type=TYPE_NAMES[column.type_code])
                            for column in description
                        )
                        return QueryResult(columns=columns, rows=rows, truncated=truncated)
        except ExecutionFailure:
            raise
        except psycopg.Error as exc:
            raise map_database_error(exc) from exc
        except DBAPIError as exc:
            if isinstance(exc.orig, psycopg.Error):
                raise map_database_error(exc.orig) from exc
            raise ExecutionFailure("analytics_unavailable", "The analytics database is unavailable.") from exc
        except Exception as exc:
            raise ExecutionFailure("internal_error", "The query could not be completed.") from exc

    def check_ready(self, dataset: DatasetContract) -> bool:
        try:
            with self._engine.connect() as connection:
                read_only = connection.exec_driver_sql("SHOW default_transaction_read_only").scalar_one()
                migration = connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()
                marker = connection.exec_driver_sql(
                    """
                    SELECT contract_sha256, manifest_sha256, row_counts
                    FROM maintenance.dataset_seeds
                    WHERE dataset = %s AND version = %s
                    """,
                    (dataset.manifest["dataset"], dataset.manifest["version"]),
                ).one_or_none()
                return bool(
                    read_only == "on"
                    and migration == "analytics_0001"
                    and marker
                    and marker[0].strip() == dataset.contract_sha256
                    and marker[1].strip() == dataset.manifest_sha256
                    and marker[2] == dataset.contract["expected_counts"]
                )
        except Exception:
            return False


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
