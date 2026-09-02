import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row

from decisionharbor.domain import (
    RESULT_RETENTION,
    ExecutionOwnership,
    QueryColumn,
    QueryResult,
    QueryRun,
    QueryRunCreation,
    StoredResult,
    CANCEL_OUTCOME_CANCELLED,
    CANCEL_OUTCOME_NOT_CANCELLABLE,
    CANCEL_OUTCOME_NOT_FOUND,
    CANCEL_OUTCOME_TERMINAL,
    finished_fields,
)
from decisionharbor.result_snapshot import encode_result_snapshot
from decisionharbor.retention import RETENTION_PREDICATE_SQL


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

VALID_EXECUTION_OWNERSHIP_SQL = """
  AND current_generation = :generation
  AND owner_worker_id = :worker_id
  AND lease_expires_at > now()
  AND EXISTS (
      SELECT 1
      FROM query_execution_attempts AS attempt
      WHERE attempt.query_run_id = query_runs.id
        AND attempt.generation = :generation
        AND attempt.worker_id = :worker_id
        AND attempt.released_at IS NULL
  )
"""

CURRENT_EXECUTION_ATTEMPT_SQL = """
WHERE query_run_id = CAST(:id AS uuid)
  AND generation = :generation
  AND worker_id = :worker_id
  AND released_at IS NULL
"""

SELECT_RUN_WITH_RESULT = text(
    f"""
    SELECT run.*,
           result.columns_json AS result_columns,
           result.rows_json AS result_rows,
           result.truncated AS result_truncated_snapshot,
           COALESCE(
               {RETENTION_PREDICATE_SQL},
               false
           ) AS result_expired
    FROM query_runs AS run
    LEFT JOIN query_results AS result ON result.query_run_id = run.id
    WHERE run.id = CAST(:id AS uuid)
    """
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

    def cancel(self, run_id: str) -> tuple[str, QueryRun | None]:
        """Conditionally record cancellation while serializing with worker claims."""

        with self._engine.begin() as connection:
            row = connection.execute(
                text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                {"id": run_id},
            ).one_or_none()
            if row is None:
                return CANCEL_OUTCOME_NOT_FOUND, None

            run = _row_to_query_run(row)
            if run.status == "queued":
                fields = finished_fields(run)
                cancelled = connection.execute(
                    text(
                        """
                        UPDATE query_runs
                        SET status = 'cancelled', finished_at = :finished_at, duration_ms = :duration_ms
                        WHERE id = CAST(:id AS uuid) AND status = 'queued'
                        RETURNING *
                        """
                    ),
                    {"id": run_id, **fields},
                ).one()
                return CANCEL_OUTCOME_CANCELLED, _row_to_query_run(cancelled)
            if run.status in {"cancelled", "succeeded"}:
                return CANCEL_OUTCOME_TERMINAL, run
            return CANCEL_OUTCOME_NOT_CANCELLABLE, run

    def claim_next(
        self,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
        max_execution_attempts: int = 3,
    ) -> ExecutionOwnership | None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must contain between 1 and 128 characters")
        if max_concurrency <= 0 or lease_ms <= 0 or max_execution_attempts <= 0:
            raise ValueError("ownership limits must be positive")
        with self._engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(1146111311)"))
            exhausted = connection.execute(
                text(
                    """
                    SELECT id, current_generation
                    FROM query_runs
                    WHERE status = 'running'
                      AND lease_expires_at <= now()
                      AND current_generation >= :max_execution_attempts
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {"max_execution_attempts": max_execution_attempts},
            ).one_or_none()
            if exhausted is not None:
                exhausted_values = exhausted._mapping
                connection.execute(
                    text(
                        """
                        UPDATE query_execution_attempts
                        SET released_at = now(), release_reason = 'attempts_exhausted'
                        WHERE query_run_id = :run_id
                          AND generation = :generation
                          AND released_at IS NULL
                        """
                    ),
                    {
                        "run_id": exhausted_values["id"],
                        "generation": exhausted_values["current_generation"],
                    },
                )
                connection.execute(
                    text(
                        """
                        UPDATE query_runs
                        SET status = 'failed',
                            error_code = 'execution_attempts_exhausted',
                            error_summary = 'Automatic execution attempts were exhausted.',
                            finished_at = now(),
                            duration_ms = GREATEST(
                                0,
                                (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer
                            ),
                            owner_worker_id = NULL,
                            heartbeat_at = NULL,
                            lease_expires_at = NULL
                        WHERE id = :run_id
                        """
                    ),
                    {"run_id": exhausted_values["id"]},
                )
            valid_ownerships = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM query_runs
                    WHERE status = 'running' AND lease_expires_at > now()
                    """
                )
            ).scalar_one()
            if valid_ownerships >= max_concurrency:
                return None
            candidate = connection.execute(
                text(
                    """
                    SELECT *
                    FROM query_runs
                    WHERE (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at <= now())
                    )
                      AND current_generation < :max_execution_attempts
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {"max_execution_attempts": max_execution_attempts},
            ).one_or_none()
            if candidate is None:
                return None
            candidate_values = candidate._mapping
            if candidate_values["status"] == "running":
                connection.execute(
                    text(
                        """
                        UPDATE query_execution_attempts
                        SET released_at = now(), release_reason = 'lease_expired'
                        WHERE query_run_id = :run_id
                          AND generation = :generation
                          AND released_at IS NULL
                        """
                    ),
                    {
                        "run_id": candidate_values["id"],
                        "generation": candidate_values["current_generation"],
                    },
                )
            row = connection.execute(
                text(
                    """
                    UPDATE query_runs AS run
                    SET status = 'running',
                        started_at = COALESCE(run.started_at, now()),
                        current_generation = run.current_generation + 1,
                        owner_worker_id = :worker_id,
                        heartbeat_at = now(),
                        lease_expires_at = now() + :lease_ms * interval '1 millisecond'
                    WHERE run.id = :run_id
                    RETURNING run.*
                    """
                ),
                {
                    "run_id": candidate_values["id"],
                    "worker_id": worker_id,
                    "lease_ms": lease_ms,
                },
            ).one()
            values = row._mapping
            connection.execute(
                text(
                    """
                    INSERT INTO query_execution_attempts (
                        query_run_id, generation, worker_id, claimed_at,
                        heartbeat_at, lease_expires_at
                    ) VALUES (
                        :run_id, :generation, :worker_id, now(),
                        :heartbeat_at, :lease_expires_at
                    )
                    """
                ),
                {
                    "run_id": values["id"],
                    "generation": values["current_generation"],
                    "worker_id": worker_id,
                    "heartbeat_at": values["heartbeat_at"],
                    "lease_expires_at": values["lease_expires_at"],
                },
            )
        return _row_to_ownership(row)

    def renew_lease(self, ownership: ExecutionOwnership, lease_ms: int) -> ExecutionOwnership | None:
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE query_runs
                    SET heartbeat_at = now(),
                        lease_expires_at = now() + :lease_ms * interval '1 millisecond'
                    WHERE id = CAST(:id AS uuid)
                      AND status = 'running'
                      {VALID_EXECUTION_OWNERSHIP_SQL}
                    RETURNING *
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                    "lease_ms": lease_ms,
                },
            ).one_or_none()
            if row is None:
                return None
            values = row._mapping
            connection.execute(
                text(
                    f"""
                    UPDATE query_execution_attempts
                    SET heartbeat_at = :heartbeat_at, lease_expires_at = :lease_expires_at
                    {CURRENT_EXECUTION_ATTEMPT_SQL}
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                    "heartbeat_at": values["heartbeat_at"],
                    "lease_expires_at": values["lease_expires_at"],
                },
            )
        return _row_to_ownership(row)

    def release_ownership(self, ownership: ExecutionOwnership) -> bool:
        with self._engine.begin() as connection:
            released = connection.execute(
                text(
                    """
                    UPDATE query_runs
                    SET owner_worker_id = NULL, lease_expires_at = now()
                    WHERE id = CAST(:id AS uuid)
                      AND status = 'running'
                      AND current_generation = :generation
                      AND owner_worker_id = :worker_id
                    RETURNING id
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            ).one_or_none()
            if released is None:
                return False
            connection.execute(
                text(
                    f"""
                    UPDATE query_execution_attempts
                    SET released_at = now(), release_reason = 'worker_stopped'
                    {CURRENT_EXECUTION_ATTEMPT_SQL}
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            )
        return True

    def release_for_next_attempt(self, ownership: ExecutionOwnership) -> bool:
        with self._engine.begin() as connection:
            released = connection.execute(
                text(
                    f"""
                    UPDATE query_runs
                    SET owner_worker_id = NULL,
                        heartbeat_at = NULL,
                        lease_expires_at = now()
                    WHERE id = CAST(:id AS uuid)
                      AND status = 'running'
                      {VALID_EXECUTION_OWNERSHIP_SQL}
                    RETURNING id
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            ).one_or_none()
            if released is None:
                return False
            connection.execute(
                text(
                    f"""
                    UPDATE query_execution_attempts
                    SET released_at = now(), release_reason = 'analytics_unavailable'
                    {CURRENT_EXECUTION_ATTEMPT_SQL}
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            )
        return True

    def publish_success(self, ownership: ExecutionOwnership, result: QueryResult) -> QueryRun:
        encoded_result = encode_result_snapshot(result)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE query_runs
                    SET status = 'succeeded',
                        returned_row_count = :returned_row_count,
                        result_truncated = :result_truncated,
                        finished_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer),
                        owner_worker_id = NULL,
                        heartbeat_at = NULL,
                        lease_expires_at = NULL
                    WHERE id = CAST(:id AS uuid)
                      AND status = 'running'
                      {VALID_EXECUTION_OWNERSHIP_SQL}
                    RETURNING *
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                    "returned_row_count": len(result.rows),
                    "result_truncated": result.truncated,
                },
            ).one_or_none()
            if row is None:
                raise StateConflict("query run success publication did not match valid ownership")
            connection.execute(
                text(
                    f"""
                    UPDATE query_execution_attempts
                    SET released_at = now(), release_reason = 'succeeded'
                    {CURRENT_EXECUTION_ATTEMPT_SQL}
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO query_results (query_run_id, columns_json, rows_json, truncated)
                    VALUES (CAST(:id AS uuid), CAST(:columns_json AS jsonb), CAST(:rows_json AS jsonb), :truncated)
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "columns_json": encoded_result.columns_json,
                    "rows_json": encoded_result.rows_json,
                    "truncated": result.truncated,
                },
            )
        return _row_to_query_run(row)

    def publish_failure(
        self,
        ownership: ExecutionOwnership,
        code: str,
        summary: str,
    ) -> QueryRun:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE query_runs
                    SET status = 'failed',
                        error_code = :code,
                        error_summary = :summary,
                        finished_at = now(),
                        duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer),
                        owner_worker_id = NULL,
                        heartbeat_at = NULL,
                        lease_expires_at = NULL
                    WHERE id = CAST(:id AS uuid)
                      AND status = 'running'
                      {VALID_EXECUTION_OWNERSHIP_SQL}
                    RETURNING *
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                    "code": code,
                    "summary": summary,
                },
            ).one_or_none()
            if row is None:
                raise StateConflict("query run failure publication did not match valid ownership")
            connection.execute(
                text(
                    f"""
                    UPDATE query_execution_attempts
                    SET released_at = now(), release_reason = 'failed'
                    {CURRENT_EXECUTION_ATTEMPT_SQL}
                    """
                ),
                {
                    "id": ownership.query_run.id,
                    "generation": ownership.generation,
                    "worker_id": ownership.worker_id,
                },
            )
        return _row_to_query_run(row)

    def get_result_state(self, run_id: str) -> StoredResult | None:
        """Read run facts and its snapshot under one database-time decision."""

        with self._engine.connect() as connection:
            row = connection.execute(
                SELECT_RUN_WITH_RESULT,
                {"id": run_id, "retention_seconds": RESULT_RETENTION.total_seconds()},
            ).one_or_none()
        if row is None:
            return None
        values = row._mapping
        expired = bool(values["result_expired"])
        return StoredResult(
            run=_row_to_query_run(row),
            snapshot=None if expired else _snapshot_from_row(row),
            result_expired=expired,
        )

    def get_result(self, run_id: str) -> QueryResult | None:
        """Return a retained snapshot for callers using the legacy read API."""

        state = self.get_result_state(run_id)
        return state.snapshot if state is not None else None


def _snapshot_from_row(row: Row) -> QueryResult | None:
    values = row._mapping
    columns = values["result_columns"]
    if columns is None:
        return None
    return QueryResult(
        columns=tuple(QueryColumn(name=column["name"], type=column["type"]) for column in columns),
        rows=tuple(tuple(cell for cell in result_row) for result_row in values["result_rows"]),
        truncated=bool(values["result_truncated_snapshot"]),
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


def _row_to_ownership(row: Row) -> ExecutionOwnership:
    values = row._mapping
    return ExecutionOwnership(
        query_run=_row_to_query_run(row),
        worker_id=values["owner_worker_id"],
        generation=values["current_generation"],
        heartbeat_at=values["heartbeat_at"],
        lease_expires_at=values["lease_expires_at"],
    )
