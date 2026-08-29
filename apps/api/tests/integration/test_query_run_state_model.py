from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import rows


pytestmark = pytest.mark.integration


NOW = datetime.now(timezone.utc)
LEASE_EXPIRES_AT = NOW + timedelta(seconds=15)
RUN_COLUMNS: Sequence[str] = (
    "status",
    "cancellation_requested_at",
    "execution_attempt_count",
    "attempt_number",
    "attempt_worker_id",
    "attempt_generation",
    "lease_expires_at",
    "heartbeat_at",
    "retry_of",
)


RUN_BASE = {
    "raw_sql": "SELECT 1",
    "status": "received",
    "policy_decision": "not_evaluated",
    "policy_version": "policy-v1",
    "statement_timeout_ms": 5_000,
    "max_rows": 500,
    "created_at": NOW,
}

OWNERSHIP = {
    "execution_attempt_count": 1,
    "attempt_number": 1,
    "attempt_worker_id": "worker-test",
    "attempt_generation": 1,
    "lease_expires_at": LEASE_EXPIRES_AT,
    "heartbeat_at": NOW,
}

VALID_FACTS_BY_STATUS = {
    "received": {},
    "queued": {"policy_decision": "allowed"},
    "rejected": {
        "policy_decision": "rejected",
        "finished_at": NOW,
        "duration_ms": 1,
        "error_code": "sql_object_not_allowed",
        "error_summary": "Object is not allowed.",
    },
    "running": {"policy_decision": "allowed", "started_at": NOW, **OWNERSHIP},
    "cancelling": {
        "policy_decision": "allowed",
        "started_at": NOW,
        "cancellation_requested_at": NOW,
        **OWNERSHIP,
    },
    "succeeded": {
        "policy_decision": "allowed",
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 2,
        "returned_row_count": 1,
        "result_truncated": False,
        **OWNERSHIP,
    },
    "failed": {
        "finished_at": NOW,
        "duration_ms": 3,
        "error_code": "query_timeout",
        "error_summary": "The query exceeded its time limit.",
    },
    "cancelled": {
        "finished_at": NOW,
        "duration_ms": 4,
        "cancellation_requested_at": NOW,
    },
}

ILLEGAL_FACTS = {
    "queued without an allowing policy decision": {
        "status": "queued",
        "policy_decision": "not_evaluated",
    },
    "queued with result facts": {
        "status": "queued",
        "policy_decision": "allowed",
        "returned_row_count": 1,
    },
    "running without execution ownership": {"status": "running", "policy_decision": "allowed", "started_at": NOW},
    "running with a partial execution ownership": {
        "status": "running",
        "policy_decision": "allowed",
        "started_at": NOW,
        "attempt_number": 1,
        "attempt_worker_id": "worker-test",
    },
    "running with a cancellation intent": {
        "status": "running",
        "policy_decision": "allowed",
        "started_at": NOW,
        "cancellation_requested_at": NOW,
        **OWNERSHIP,
    },
    "cancelling without a cancellation intent": {
        "status": "cancelling",
        "policy_decision": "allowed",
        "started_at": NOW,
        **OWNERSHIP,
    },
    "succeeded without result facts": {
        "status": "succeeded",
        "policy_decision": "allowed",
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 2,
        "execution_attempt_count": 1,
    },
    "succeeded with an error": {
        "status": "succeeded",
        "policy_decision": "allowed",
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 2,
        "returned_row_count": 1,
        "result_truncated": False,
        "execution_attempt_count": 1,
        "error_code": "query_timeout",
    },
    "failed without an error": {
        "status": "failed",
        "finished_at": NOW,
        "duration_ms": 3,
    },
    "cancelled without a cancellation intent": {
        "status": "cancelled",
        "finished_at": NOW,
        "duration_ms": 4,
    },
    "terminal status without a finish time": {
        "status": "rejected",
        "policy_decision": "rejected",
        "error_code": "sql_object_not_allowed",
        "error_summary": "Object is not allowed.",
    },
}

TERMINAL_STATUSES = ("rejected", "succeeded", "failed", "cancelled")


def platform_admin_url() -> str:
    return os.environ["TEST_ADMIN_DATABASE_URL"].rsplit("/", 1)[0] + "/platform"


@contextmanager
def platform_connection():
    """Keep every fact written by these tests invisible to the running worker.

    A committed `queued` run would be claimed and executed by the worker, so the
    state model is verified inside a transaction that is always rolled back.
    """
    with psycopg.connect(platform_admin_url()) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def insert_run(connection: psycopg.Connection, **overrides: object) -> str:
    values = {**RUN_BASE, "id": uuid4(), **overrides}
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({name})s" for name in values)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO query_runs ({columns}) VALUES ({placeholders})",
            values,
        )
    return str(values["id"])


def insert_result(connection: psycopg.Connection, run_id: str) -> None:
    connection.execute(
        """
        INSERT INTO query_run_results
            (query_run_id, result_columns, result_rows, truncated, created_at)
        VALUES (%s, '[{"name":"id","type":"integer"}]', '[[1]]', false, now())
        """,
        (run_id,),
    )


def update_run(connection: psycopg.Connection, run_id: str, **changes: object) -> None:
    """Apply an update, undoing only itself when a guard rejects it.

    The savepoint keeps the surrounding transaction usable, so a test can keep
    asserting on the same run after an illegal transition.
    """
    assignments = ", ".join(f"{name} = %({name})s" for name in changes)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE query_runs SET {assignments} WHERE id = %(id)s",
                {**changes, "id": run_id},
            )


def read_run(connection: psycopg.Connection, run_id: str) -> dict[str, object]:
    columns = ", ".join(RUN_COLUMNS)
    with connection.cursor(row_factory=rows.dict_row) as cursor:
        cursor.execute(f"SELECT {columns} FROM query_runs WHERE id = %s", (run_id,))
        return cursor.fetchone()


@pytest.mark.parametrize("status", sorted(VALID_FACTS_BY_STATUS))
def test_state_model_accepts_every_lifecycle_status(status: str) -> None:
    with platform_connection() as connection:
        run_id = insert_run(connection, status=status, **VALID_FACTS_BY_STATUS[status])

        assert read_run(connection, run_id)["status"] == status


@pytest.mark.parametrize("case", sorted(ILLEGAL_FACTS))
def test_illegal_state_facts_are_rejected_by_database_constraints(case: str) -> None:
    with platform_connection() as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_run(connection, **ILLEGAL_FACTS[case])


@pytest.mark.parametrize(("status", "next_status"), [(status, "running") for status in TERMINAL_STATUSES])
def test_terminal_statuses_cannot_move_to_another_status(status: str, next_status: str) -> None:
    with platform_connection() as connection:
        run_id = insert_run(connection, status=status, **VALID_FACTS_BY_STATUS[status])

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, status=next_status)

        assert read_run(connection, run_id)["status"] == status


def test_execution_attempt_records_ownership_lease_and_heartbeat() -> None:
    with platform_connection() as connection:
        run_id = insert_run(connection, status="queued", policy_decision="allowed")

        update_run(
            connection,
            run_id,
            status="running",
            started_at=NOW,
            **OWNERSHIP,
        )

        facts = read_run(connection, run_id)
        assert facts["status"] == "running"
        assert facts["execution_attempt_count"] == 1
        assert facts["attempt_number"] == 1
        assert facts["attempt_worker_id"] == "worker-test"
        assert facts["attempt_generation"] == 1
        assert facts["lease_expires_at"] is not None
        assert facts["heartbeat_at"] is not None


def test_execution_attempt_count_and_generation_never_move_backwards() -> None:
    with platform_connection() as connection:
        run_id = insert_run(
            connection,
            status="running",
            policy_decision="allowed",
            started_at=NOW,
            **OWNERSHIP,
        )

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, execution_attempt_count=0)
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, attempt_generation=0)
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, attempt_number=0)

        update_run(
            connection,
            run_id,
            execution_attempt_count=2,
            attempt_number=2,
            attempt_generation=2,
            attempt_worker_id="worker-second",
        )

        facts = read_run(connection, run_id)
        assert facts["execution_attempt_count"] == 2
        assert facts["attempt_number"] == 2
        assert facts["attempt_generation"] == 2
        assert facts["attempt_worker_id"] == "worker-second"


def test_cancellation_intent_is_recorded_before_cancelling() -> None:
    with platform_connection() as connection:
        run_id = insert_run(
            connection, status="running", policy_decision="allowed", started_at=NOW, **OWNERSHIP
        )

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, status="cancelling")

        update_run(connection, run_id, status="cancelling", cancellation_requested_at=NOW)

        assert read_run(connection, run_id)["cancellation_requested_at"] is not None


def test_cancellation_intent_cannot_be_discarded_or_overtaken_by_a_new_attempt() -> None:
    with platform_connection() as connection:
        run_id = insert_run(
            connection, status="running", policy_decision="allowed", started_at=NOW, **OWNERSHIP
        )
        update_run(connection, run_id, status="cancelling", cancellation_requested_at=NOW)

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, cancellation_requested_at=None)
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, status="running")
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, attempt_number=2, attempt_generation=2)
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, run_id, execution_attempt_count=2)

        update_run(connection, run_id, status="cancelled", finished_at=NOW, duration_ms=5)

        assert read_run(connection, run_id)["status"] == "cancelled"


def test_success_requires_a_recorded_execution_attempt() -> None:
    with platform_connection() as connection:
        run_id = insert_run(connection, status="queued", policy_decision="allowed")

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(
                connection,
                run_id,
                status="succeeded",
                started_at=NOW,
                finished_at=NOW,
                duration_ms=2,
                returned_row_count=1,
                result_truncated=False,
                execution_attempt_count=1,
            )

        update_run(connection, run_id, status="running", started_at=NOW, **OWNERSHIP)
        insert_result(connection, run_id)
        update_run(
            connection,
            run_id,
            status="succeeded",
            finished_at=NOW,
            duration_ms=2,
            returned_row_count=1,
            result_truncated=False,
        )

        assert read_run(connection, run_id)["status"] == "succeeded"


def test_success_requires_a_result_snapshot() -> None:
    with platform_connection() as connection:
        run_id = insert_run(
            connection, status="running", policy_decision="allowed", started_at=NOW, **OWNERSHIP
        )

        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(
                connection,
                run_id,
                status="succeeded",
                finished_at=NOW,
                duration_ms=2,
                returned_row_count=1,
                result_truncated=False,
            )

        assert read_run(connection, run_id)["status"] == "running"
        insert_result(connection, run_id)
        update_run(
            connection,
            run_id,
            status="succeeded",
            finished_at=NOW,
            duration_ms=2,
            returned_row_count=1,
            result_truncated=False,
        )

        assert read_run(connection, run_id)["status"] == "succeeded"


def test_retry_runs_keep_their_source_link() -> None:
    with platform_connection() as connection:
        source_run_id = insert_run(
            connection,
            status="failed",
            finished_at=NOW,
            duration_ms=3,
            error_code="query_timeout",
            error_summary="The query exceeded its time limit.",
        )

        retry_run_id = insert_run(
            connection, status="queued", policy_decision="allowed", retry_of=source_run_id
        )

        assert str(read_run(connection, retry_run_id)["retry_of"]) == source_run_id
        with pytest.raises(psycopg.errors.CheckViolation):
            update_run(connection, retry_run_id, retry_of=retry_run_id)


IDEMPOTENCY_INSERT = """
    INSERT INTO query_run_idempotency
        (scope, idempotency_key, request_fingerprint, query_run_id, created_at)
    VALUES (%s, %s, 'fingerprint', %s, %s)
"""


def test_idempotency_keys_are_scoped_and_bounded() -> None:
    retry_scope = f"retry:{uuid4()}"
    with platform_connection() as connection:
        run_id = insert_run(connection, status="queued", policy_decision="allowed")

        connection.execute(IDEMPOTENCY_INSERT, ("submit", "same-key", run_id, NOW))
        connection.execute(IDEMPOTENCY_INSERT, (retry_scope, "same-key", run_id, NOW))

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(IDEMPOTENCY_INSERT, ("submit", "same-key", run_id, NOW))

        for scope, key in (("submit", "k" * 129), ("other-scope", "key")):
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(IDEMPOTENCY_INSERT, (scope, key, run_id, NOW))
