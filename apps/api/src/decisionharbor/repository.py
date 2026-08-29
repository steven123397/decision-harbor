import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row

from decisionharbor.domain import QueryRun


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
        "cancellation_requested_at",
        "execution_attempt_count",
        "attempt_number",
        "attempt_worker_id",
        "attempt_generation",
        "lease_expires_at",
        "heartbeat_at",
        "retry_of",
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
    ) -> QueryRun:
        run = QueryRun.received(raw_sql, policy_version, statement_timeout_ms, max_rows)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO query_runs (
                        id, raw_sql, status, policy_decision, policy_version,
                        referenced_objects, statement_timeout_ms, max_rows, created_at
                    ) VALUES (
                        CAST(:id AS uuid), :raw_sql, :status, :policy_decision, :policy_version,
                        CAST(:referenced_objects AS jsonb), :statement_timeout_ms, :max_rows, :created_at
                    )
                    RETURNING *
                    """
                ),
                {
                    **run.__dict__,
                    "referenced_objects": json.dumps(run.referenced_objects),
                },
            ).one()
        return _row_to_query_run(row)

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

    def recover_interrupted(self) -> int:
        with self._engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE query_runs
                    SET status = 'failed',
                        error_code = 'execution_interrupted',
                        error_summary = 'Execution was interrupted before completion.',
                        finished_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
                    WHERE status = 'received'
                    """
                )
            )
        return result.rowcount

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
        cancellation_requested_at=values["cancellation_requested_at"],
        execution_attempt_count=values["execution_attempt_count"],
        attempt_number=values["attempt_number"],
        attempt_worker_id=values["attempt_worker_id"],
        attempt_generation=values["attempt_generation"],
        lease_expires_at=values["lease_expires_at"],
        heartbeat_at=values["heartbeat_at"],
        retry_of=str(values["retry_of"]) if values["retry_of"] else None,
    )
