from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.domain import (
    QueryResult,
    QueryRun,
    encode_json,
    encode_snapshot_columns,
    finished_fields,
)
from decisionharbor.repository import row_to_query_run


# Claiming coordinates the shared capacity through one transaction-scoped
# advisory lock: the count of valid execution ownerships and the claim that
# follows it must not interleave with another replica's, or two replicas could
# each read room for one more and both take it.
CLAIM_LOCK_KEY = 7_250_401

CLAIM_LOCK = text("SELECT pg_advisory_xact_lock(CAST(:claim_lock_key AS bigint))")

# Every right-hand side of the SET list reads the pre-update row, so
# attempt_number matches the new attempt count and the generation moves forward
# by one from the generation the run carried while it was queued or owned.
# `started_at` is left alone when it is already set, because a run taken over
# from a lost lease began executing with its first attempt, not with the one
# that recovers it.
#
# A run is claimable while it is queued, or while the replica that owned it let
# its lease expire. A run whose lease was lost comes first: it has waited the
# longest and must not be starved by a busy queue.
#
# The capacity guard counts only valid execution ownerships: runs whose current
# attempt still holds an unexpired lease. A superseded attempt whose database
# work has not physically stopped is not one, so recovery never has to wait for
# confirmation that old SQL is gone.
CLAIM_NEXT_RUN = text(
    """
    UPDATE query_runs
    SET status = 'running',
        started_at = COALESCE(started_at, now()),
        execution_attempt_count = execution_attempt_count + 1,
        attempt_number = execution_attempt_count + 1,
        attempt_generation = COALESCE(attempt_generation, 0) + 1,
        attempt_worker_id = :worker_id,
        lease_expires_at = now() + CAST(:lease_ms AS integer) * INTERVAL '1 millisecond',
        heartbeat_at = now()
    WHERE id = (
        SELECT id FROM query_runs
        WHERE status = 'queued'
           OR (status = 'running' AND lease_expires_at <= now())
        ORDER BY (status = 'running') DESC, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
      AND (
        SELECT count(*) FROM query_runs
        WHERE status IN ('running', 'cancelling') AND lease_expires_at > now()
      ) < CAST(:max_concurrency AS integer)
    RETURNING *
    """
)

# Renewal is fenced by the exact execution attempt, so a replica that lost a
# run to a takeover stops extending it instead of quietly taking it back from
# its new owner. An expired lease cannot be renewed: the ownership it carried
# is already released.
RENEW_LEASE = text(
    """
    UPDATE query_runs
    SET lease_expires_at = now() + CAST(:lease_ms AS integer) * INTERVAL '1 millisecond',
        heartbeat_at = now()
    WHERE id = CAST(:id AS uuid)
      AND status IN ('running', 'cancelling')
      AND attempt_number = :attempt_number
      AND attempt_generation = :attempt_generation
      AND lease_expires_at > now()
    RETURNING *
    """
)

# A snapshot is written before the fenced update that succeeds the run, so a
# superseded attempt reaches this insert with the run already published by its
# new owner. The conflict is then a no-op instead of an error: the fence below
# still decides, and the transaction rolls the whole publish back.
INSERT_RESULT_SNAPSHOT = text(
    """
    INSERT INTO query_run_results (
        query_run_id, result_columns, result_rows, truncated, created_at
    ) VALUES (
        CAST(:query_run_id AS uuid), CAST(:result_columns AS jsonb),
        CAST(:result_rows AS jsonb), :truncated, :created_at
    )
    ON CONFLICT (query_run_id) DO NOTHING
    """
)

# Both terminals share one fence, so a lost execution attempt can never be
# widened for one of them without widening it for the other. The fence asks for
# a valid execution ownership: the run is still running, this worker still owns
# the attempt no takeover has superseded, and the lease it holds has not
# expired. Without the lease a replica that stalled past its lease could
# publish over the run another replica has already recovered.
PUBLISH_FENCE = """
    WHERE id = CAST(:id AS uuid)
      AND status = 'running'
      AND attempt_number = :attempt_number
      AND attempt_generation = :attempt_generation
      AND lease_expires_at > now()
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
    """Claims query runs and publishes their terminal state.

    Every publish is fenced by the claimed execution attempt: an update only
    applies while this worker still owns the run, so a taken over or expired
    attempt discards its outcome instead of overwriting the newer decision.
    """

    def __init__(
        self,
        database_url: str,
        worker_id: str,
        lease_ms: int,
        max_concurrency: int,
    ) -> None:
        self._worker_id = worker_id
        self._lease_ms = lease_ms
        self._max_concurrency = max_concurrency
        # A claimed run needs its own connection to publish on and another to
        # renew its lease on, alongside the connection the poll loop claims
        # with, and every one of them may be busy at once.
        self._engine: Engine = create_engine(
            database_url,
            pool_size=max_concurrency + 2,
            max_overflow=0,
            pool_pre_ping=True,
        )

    def claim(self) -> QueryRun | None:
        """Take ownership of the next run this worker may own.

        Reports None both when no run is waiting and when the shared capacity
        is used up: the database counts the valid execution ownerships inside
        the same transaction that claims, so the limit no replica may cross is
        decided where every replica can see it, not in this process.
        """
        with self._engine.begin() as connection:
            connection.execute(CLAIM_LOCK, {"claim_lock_key": CLAIM_LOCK_KEY})
            row = connection.execute(
                CLAIM_NEXT_RUN,
                {
                    "worker_id": self._worker_id,
                    "lease_ms": self._lease_ms,
                    "max_concurrency": self._max_concurrency,
                },
            ).one_or_none()
        return row_to_query_run(row) if row else None

    def renew_lease(self, run: QueryRun) -> bool:
        """Extend the lease of an execution this worker is still running.

        Reports whether the ownership was renewed. A run that another replica
        took over, or whose lease already lapsed, no longer matches and is
        left alone, so the caller can stop renewing what it no longer owns.
        """
        with self._engine.begin() as connection:
            renewed = connection.execute(
                RENEW_LEASE,
                {**_ownership(run), "lease_ms": self._lease_ms},
            ).one_or_none()
        return renewed is not None

    def publish_success(self, run: QueryRun, result: QueryResult) -> bool:
        """Atomically store the result snapshot and the succeeded terminal state."""
        finished = finished_fields(run)
        with self._engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                INSERT_RESULT_SNAPSHOT,
                {
                    "query_run_id": run.id,
                    # The measured bytes and the stored snapshot share one
                    # encoding, so a stored snapshot is never larger than the
                    # budget the builder spent.
                    "result_columns": encode_snapshot_columns(result.columns).decode("utf-8"),
                    "result_rows": encode_json([list(row) for row in result.rows]).decode("utf-8"),
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
