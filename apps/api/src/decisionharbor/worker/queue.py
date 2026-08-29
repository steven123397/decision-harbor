import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.domain import QueryResult, QueryRun, finished_fields
from decisionharbor.repository import row_to_query_run


CLAIM_QUEUED_RUN = text(
    """
    UPDATE query_runs
    SET status = 'running',
        started_at = now(),
        execution_attempt_count = execution_attempt_count + 1,
        attempt_number = execution_attempt_count + 1,
        attempt_generation = COALESCE(attempt_generation, 0) + 1,
        attempt_worker_id = :worker_id,
        lease_expires_at = now() + CAST(:lease_ms AS integer) * INTERVAL '1 millisecond',
        heartbeat_at = now()
    WHERE id = (
        SELECT id FROM query_runs
        WHERE status = 'queued'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *
    """
)

INSERT_RESULT_SNAPSHOT = text(
    """
    INSERT INTO query_run_results (
        query_run_id, result_columns, result_rows, truncated, created_at
    ) VALUES (
        CAST(:query_run_id AS uuid), CAST(:result_columns AS jsonb),
        CAST(:result_rows AS jsonb), :truncated, :created_at
    )
    """
)

# Both terminals share one fence, so a lost execution attempt can never be
# widened for one of them without widening it for the other.
PUBLISH_FENCE = """
    WHERE id = CAST(:id AS uuid)
      AND status = 'running'
      AND attempt_number = :attempt_number
      AND attempt_generation = :attempt_generation
    RETURNING *
"""

PUBLISH_SUCCESS = text(
    """
    UPDATE query_runs
    SET status = 'succeeded',
        returned_row_count = :returned_row_count,
        result_truncated = :result_truncated,
        finished_at = :finished_at,
        duration_ms = :duration_ms
    """
    + PUBLISH_FENCE
)

PUBLISH_FAILURE = text(
    """
    UPDATE query_runs
    SET status = 'failed',
        error_code = :error_code,
        error_summary = :error_summary,
        finished_at = :finished_at,
        duration_ms = :duration_ms
    """
    + PUBLISH_FENCE
)


def _ownership(run: QueryRun) -> dict[str, object]:
    return {
        "id": run.id,
        "attempt_number": run.attempt_number,
        "attempt_generation": run.attempt_generation,
    }


class QueryRunQueue:
    """Claims queued query runs and publishes their terminal state.

    Every publish is fenced by the claimed execution attempt: an update only
    applies while this worker still owns the run, so a cancelled or taken over
    run discards the result instead of overwriting the newer decision.
    """

    def __init__(self, database_url: str, worker_id: str, lease_ms: int) -> None:
        self._worker_id = worker_id
        self._lease_ms = lease_ms
        self._engine: Engine = create_engine(database_url, pool_size=2, max_overflow=0, pool_pre_ping=True)

    def claim(self) -> QueryRun | None:
        """Take ownership of the oldest queued run, or report there is none."""
        with self._engine.begin() as connection:
            row = connection.execute(
                CLAIM_QUEUED_RUN,
                {"worker_id": self._worker_id, "lease_ms": self._lease_ms},
            ).one_or_none()
        return row_to_query_run(row) if row else None

    def publish_success(self, run: QueryRun, result: QueryResult) -> bool:
        """Atomically store the result snapshot and the succeeded terminal state."""
        finished = finished_fields(run)
        with self._engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                INSERT_RESULT_SNAPSHOT,
                {
                    "query_run_id": run.id,
                    "result_columns": json.dumps(
                        [{"name": column.name, "type": column.type} for column in result.columns]
                    ),
                    "result_rows": json.dumps([list(row) for row in result.rows]),
                    "truncated": result.truncated,
                    "created_at": finished["finished_at"],
                },
            )
            published = connection.execute(
                PUBLISH_SUCCESS,
                {
                    **_ownership(run),
                    **finished,
                    "returned_row_count": len(result.rows),
                    "result_truncated": result.truncated,
                },
            ).one_or_none()
            if published is None:
                transaction.rollback()
                return False
            transaction.commit()
        return True

    def publish_failure(self, run: QueryRun, error_code: str, error_summary: str) -> bool:
        with self._engine.begin() as connection:
            published = connection.execute(
                PUBLISH_FAILURE,
                {
                    **_ownership(run),
                    **finished_fields(run),
                    "error_code": error_code,
                    "error_summary": error_summary,
                },
            ).one_or_none()
        return published is not None
