import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, RootTransaction, Row

from decisionharbor.domain import (
    RESULT_RETENTION,
    IdempotencyClaim,
    QueryColumn,
    QueryResult,
    QueryRun,
    StoredResult,
    SubmitReservation,
)


def _claim_parameters(idempotency: IdempotencyClaim) -> dict[str, str]:
    return {
        "scope": idempotency.scope,
        "idempotency_key": idempotency.key,
        "request_fingerprint": idempotency.request_fingerprint,
    }


class StateConflict(RuntimeError):
    pass


SELECT_IDEMPOTENCY = text(
    """
    SELECT query_run_id, request_fingerprint
    FROM query_run_idempotency
    WHERE scope = :scope AND idempotency_key = :idempotency_key
    """
)

INSERT_RUN = text(
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
)

INSERT_IDEMPOTENCY = text(
    """
    INSERT INTO query_run_idempotency (
        scope, idempotency_key, request_fingerprint, query_run_id, created_at
    ) VALUES (
        :scope, :idempotency_key, :request_fingerprint, CAST(:query_run_id AS uuid), :created_at
    )
    ON CONFLICT (scope, idempotency_key) DO NOTHING
    RETURNING query_run_id
    """
)

SELECT_RUN = text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid)")

# A run and its snapshot are read together so a result read never falls back to
# the analytics database: the snapshot is either stored or it is not. Whether it
# is still retained is decided here as well, from the `finished_at` this database
# recorded and the clock this database keeps, so a read and the cleanup that
# follows it can never disagree about the retention window.
SELECT_RUN_WITH_RESULT = text(
    """
    SELECT run.*,
           result.result_columns, result.result_rows, result.truncated,
           COALESCE(
               run.finished_at
                   + CAST(:retention_seconds AS double precision) * INTERVAL '1 second'
                   <= now(),
               false
           ) AS result_expired
    FROM query_runs AS run
    LEFT JOIN query_run_results AS result ON result.query_run_id = run.id
    WHERE run.id = CAST(:id AS uuid)
    """
)


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

    def reserve(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        *,
        idempotency: IdempotencyClaim | None = None,
    ) -> SubmitReservation:
        """Persist a received query run, replaying it when its key was already used.

        The run and its idempotency record are written in one transaction. A key
        that another request reserved first rolls the new run back, so a replay
        never leaves an unrecorded duplicate in the queue.
        """
        run = QueryRun.received(raw_sql, policy_version, statement_timeout_ms, max_rows)
        with self._engine.connect() as connection:
            transaction = connection.begin()
            record = self._recorded_claim(connection, idempotency)
            created: QueryRun | None = None
            if record is None:
                row = connection.execute(
                    INSERT_RUN,
                    {**run.__dict__, "referenced_objects": json.dumps(run.referenced_objects)},
                ).one()
                created = row_to_query_run(row)
                if idempotency is not None and not self._claim_key(connection, idempotency, run):
                    record = self._recorded_claim(connection, idempotency)
            if record is not None:
                return self._replay(transaction, connection, record, idempotency)
            transaction.commit()
            return SubmitReservation.created(created)

    def _recorded_claim(
        self,
        connection: Connection,
        idempotency: IdempotencyClaim | None,
    ) -> Row | None:
        if idempotency is None:
            return None
        return connection.execute(SELECT_IDEMPOTENCY, _claim_parameters(idempotency)).one_or_none()

    def _claim_key(
        self,
        connection: Connection,
        idempotency: IdempotencyClaim,
        run: QueryRun,
    ) -> bool:
        """Record the key, reporting False when another request claimed it first."""
        parameters = {
            **_claim_parameters(idempotency),
            "query_run_id": run.id,
            "created_at": run.created_at,
        }
        return connection.execute(INSERT_IDEMPOTENCY, parameters).one_or_none() is not None

    def _replay(
        self,
        transaction: RootTransaction,
        connection: Connection,
        record: Row,
        idempotency: IdempotencyClaim,
    ) -> SubmitReservation:
        run = row_to_query_run(
            connection.execute(SELECT_RUN, {"id": str(record.query_run_id)}).one()
        )
        transaction.rollback()
        return SubmitReservation.replayed(
            run, record.request_fingerprint == idempotency.request_fingerprint
        )

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
        return row_to_query_run(row)

    def get(self, run_id: str) -> QueryRun | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).one_or_none()
        return row_to_query_run(row) if row else None

    def get_result(self, run_id: str) -> StoredResult | None:
        """Read a query run together with the snapshot stored for it, if any."""
        with self._engine.connect() as connection:
            row = connection.execute(
                SELECT_RUN_WITH_RESULT,
                {"id": run_id, "retention_seconds": RESULT_RETENTION.total_seconds()},
            ).one_or_none()
        if row is None:
            return None
        return StoredResult(
            run=row_to_query_run(row),
            snapshot=snapshot_from_row(row),
            result_expired=row._mapping["result_expired"],
        )

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


def snapshot_from_row(row: Row) -> QueryResult | None:
    """The snapshot a joined run row carries, or None when the run has none."""
    values = row._mapping
    columns = values["result_columns"]
    if columns is None:
        return None
    return QueryResult(
        columns=tuple(
            QueryColumn(name=column["name"], type=column["type"]) for column in columns
        ),
        rows=tuple(tuple(cell) for cell in values["result_rows"]),
        truncated=values["truncated"],
    )


def row_to_query_run(row: Row) -> QueryRun:
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
