import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun, QueryRunCreation


class StateConflict(RuntimeError):
    pass


TRANSITION_COLUMNS = frozenset(
    {
        "status",
        "policy_decision",
        "referenced_objects",
        "returned_row_count",
        "result_truncated",
        "error_code",
        "error_summary",
        "started_at",
        "finished_at",
        "duration_ms",
    }
)


class QueryRunRepository:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, pool_size=5, max_overflow=0, pool_pre_ping=True)

    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        idempotency_key: str | None = None,
    ) -> QueryRunCreation:
        run = QueryRun.received(raw_sql, policy_version, statement_timeout_ms, max_rows)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO query_runs (
                        id, raw_sql, status, policy_decision, policy_version,
                        referenced_objects, statement_timeout_ms, max_rows, created_at,
                        idempotency_key
                    ) VALUES (
                        CAST(:id AS uuid), :raw_sql, :status, :policy_decision, :policy_version,
                        CAST(:referenced_objects AS jsonb), :statement_timeout_ms, :max_rows, :created_at,
                        :idempotency_key
                    )
                    ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                    RETURNING *
                    """
                ),
                {
                    **run.__dict__,
                    "referenced_objects": json.dumps(run.referenced_objects),
                    "idempotency_key": idempotency_key,
                },
            ).one_or_none()
            if row is not None:
                return QueryRunCreation(query_run=_row_to_query_run(row), created=True)
            row = connection.execute(
                text("SELECT * FROM query_runs WHERE idempotency_key = :idempotency_key"),
                {"idempotency_key": idempotency_key},
            ).one()
        return QueryRunCreation(query_run=_row_to_query_run(row), created=False)

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        unknown = set(changes) - TRANSITION_COLUMNS
        if unknown or "status" not in changes:
            raise ValueError(f"unsupported transition fields: {sorted(unknown)}")
        assignments: list[str] = []
        parameters = {"id": run_id, "expected_status": expected_status}
        for name, value in changes.items():
            if name == "referenced_objects":
                assignments.append(f"{name} = CAST(:{name} AS jsonb)")
                parameters[name] = json.dumps(value)
            else:
                assignments.append(f"{name} = :{name}")
                parameters[name] = value
        statement = text(
            f"""
            UPDATE query_runs
            SET {', '.join(assignments)}
            WHERE id = CAST(:id AS uuid) AND status = :expected_status
            RETURNING *
            """
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement, parameters).one_or_none()
        if row is None:
            raise StateConflict("query run transition did not match expected state")
        return _row_to_query_run(row)

    def get(self, run_id: str) -> QueryRun | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).one_or_none()
        return _row_to_query_run(row) if row else None

    def claim_next(self) -> QueryRun | None:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    WITH next_run AS (
                        SELECT id
                        FROM query_runs
                        WHERE status = 'queued'
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE query_runs AS run
                    SET status = 'running', started_at = now()
                    FROM next_run
                    WHERE run.id = next_run.id AND run.status = 'queued'
                    RETURNING run.*
                    """
                )
            ).one_or_none()
        return _row_to_query_run(row) if row else None

    def publish_success(self, run_id: str, result: QueryResult) -> QueryRun:
        columns_json = json.dumps(
            [{"name": column.name, "type": column.type} for column in result.columns],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        rows_json = json.dumps(result.rows, ensure_ascii=False, separators=(",", ":"))
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE query_runs
                    SET status = 'succeeded',
                        returned_row_count = :returned_row_count,
                        result_truncated = :result_truncated,
                        finished_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
                    WHERE id = CAST(:id AS uuid) AND status = 'running'
                    RETURNING *
                    """
                ),
                {
                    "id": run_id,
                    "returned_row_count": len(result.rows),
                    "result_truncated": result.truncated,
                },
            ).one_or_none()
            if row is None:
                raise StateConflict("query run success publication did not match running state")
            connection.execute(
                text(
                    """
                    INSERT INTO query_results (query_run_id, columns_json, rows_json, truncated)
                    VALUES (CAST(:id AS uuid), CAST(:columns_json AS jsonb), CAST(:rows_json AS jsonb), :truncated)
                    """
                ),
                {
                    "id": run_id,
                    "columns_json": columns_json,
                    "rows_json": rows_json,
                    "truncated": result.truncated,
                },
            )
        return _row_to_query_run(row)

    def publish_failure(self, run_id: str, code: str, summary: str) -> QueryRun:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE query_runs
                    SET status = 'failed',
                        error_code = :code,
                        error_summary = :summary,
                        finished_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
                    WHERE id = CAST(:id AS uuid) AND status = 'running'
                    RETURNING *
                    """
                ),
                {"id": run_id, "code": code, "summary": summary},
            ).one_or_none()
        if row is None:
            raise StateConflict("query run failure publication did not match running state")
        return _row_to_query_run(row)

    def get_result(self, run_id: str) -> QueryResult | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT columns_json, rows_json, truncated
                    FROM query_results
                    WHERE query_run_id = CAST(:id AS uuid)
                    """
                ),
                {"id": run_id},
            ).one_or_none()
        if row is None:
            return None
        values = row._mapping
        return QueryResult(
            columns=tuple(QueryColumn(name=column["name"], type=column["type"]) for column in values["columns_json"]),
            rows=tuple(tuple(cell for cell in result_row) for result_row in values["rows_json"]),
            truncated=values["truncated"],
        )


def _row_to_query_run(row: Row) -> QueryRun:
    values = row._mapping
    return QueryRun(
        id=str(values["id"]),
        raw_sql=values["raw_sql"],
        status=values["status"],
        policy_decision=values["policy_decision"],
        policy_version=values["policy_version"],
        referenced_objects=tuple(values["referenced_objects"]),
        statement_timeout_ms=values["statement_timeout_ms"],
        max_rows=values["max_rows"],
        returned_row_count=values["returned_row_count"],
        result_truncated=values["result_truncated"],
        error_code=values["error_code"],
        error_summary=values["error_summary"],
        created_at=values["created_at"],
        started_at=values["started_at"],
        finished_at=values["finished_at"],
        duration_ms=values["duration_ms"],
    )
