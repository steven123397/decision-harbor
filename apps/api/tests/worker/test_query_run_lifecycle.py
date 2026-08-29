from time import monotonic, sleep
import os
from uuid import uuid4

import psycopg
from psycopg import rows
import pytest


pytestmark = pytest.mark.worker


TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
WAIT_SECONDS = 30
POLL_SECONDS = 0.1

CUSTOMER_SQL = "SELECT id, customer_code FROM customers ORDER BY id LIMIT 2"
SEMANTIC_ERROR_SQL = "SELECT missing_column FROM customers"
TIMEOUT_SQL = (
    "SELECT count(*) FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c"
)
UNSUPPORTED_TYPE_SQL = "SELECT ARRAY[1, 2] AS items"
# A read-only transaction refuses the write lock with SQLSTATE 25006, which the
# executor cannot classify and therefore reports as an internal error.
UNCLASSIFIED_SQL = "SELECT id FROM customers FOR UPDATE"


def platform_url() -> str:
    return os.environ["PLATFORM_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def insert_queued_run(raw_sql: str, *, statement_timeout_ms: int = 5_000, max_rows: int = 500) -> str:
    run_id = str(uuid4())
    with psycopg.connect(platform_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at
            ) VALUES (%s, %s, 'queued', 'allowed', 'policy-v1', %s, %s, now())
            """,
            (run_id, raw_sql, statement_timeout_ms, max_rows),
        )
    return run_id


def read_run(run_id: str) -> dict[str, object]:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT status, error_code, error_summary, started_at, finished_at, duration_ms,
                   returned_row_count, result_truncated, execution_attempt_count,
                   attempt_number, attempt_worker_id, attempt_generation
            FROM query_runs WHERE id = %s
            """,
            (run_id,),
        ).fetchone()


def read_snapshot(run_id: str) -> dict[str, object] | None:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT result_columns, result_rows, truncated
            FROM query_run_results WHERE query_run_id = %s
            """,
            (run_id,),
        ).fetchone()


def wait_for_terminal(run_id: str) -> dict[str, object]:
    deadline = monotonic() + WAIT_SECONDS
    while monotonic() < deadline:
        facts = read_run(run_id)
        if facts["status"] in TERMINAL_STATUSES:
            return facts
        sleep(POLL_SECONDS)
    raise AssertionError(f"query run {run_id} never reached a terminal state: {read_run(run_id)}")


def test_worker_claims_a_queued_run_and_publishes_its_snapshot() -> None:
    run_id = insert_queued_run(CUSTOMER_SQL)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 2
    assert facts["result_truncated"] is False
    assert facts["execution_attempt_count"] == 1
    assert facts["attempt_number"] == 1
    assert facts["attempt_generation"] == 1
    assert facts["attempt_worker_id"]
    assert facts["started_at"] is not None
    assert facts["finished_at"] is not None
    assert facts["duration_ms"] is not None
    assert facts["error_code"] is None

    snapshot = read_snapshot(run_id)
    assert snapshot is not None
    assert snapshot["result_columns"] == [
        {"name": "id", "type": "bigint"},
        {"name": "customer_code", "type": "character varying"},
    ]
    assert snapshot["result_rows"] == [["1", "CUST-0001"], ["2", "CUST-0002"]]
    assert snapshot["truncated"] is False


def test_worker_extracts_a_bounded_prefix_without_materialising_the_whole_result() -> None:
    run_id = insert_queued_run("SELECT id FROM orders ORDER BY id", max_rows=2)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 2
    assert facts["result_truncated"] is True
    assert read_snapshot(run_id)["result_rows"] == [["1"], ["2"]]


def test_semantic_failure_is_published_without_database_detail() -> None:
    run_id = insert_queued_run(SEMANTIC_ERROR_SQL)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "query_semantic_error"
    assert "missing_column" not in str(facts["error_summary"]).lower()
    assert facts["finished_at"] is not None
    assert facts["duration_ms"] is not None
    assert facts["returned_row_count"] is None
    assert read_snapshot(run_id) is None


def test_query_timeout_is_published_as_a_failed_run() -> None:
    run_id = insert_queued_run(TIMEOUT_SQL, statement_timeout_ms=1)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "query_timeout"


def test_unsupported_result_type_is_published_as_a_failed_run() -> None:
    run_id = insert_queued_run(UNSUPPORTED_TYPE_SQL)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "unsupported_result_type"
    assert read_snapshot(run_id) is None


def test_unclassified_database_failure_is_published_as_an_internal_error() -> None:
    run_id = insert_queued_run(UNCLASSIFIED_SQL)

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "internal_error"
    assert "customers" not in str(facts["error_summary"]).lower()
    assert read_snapshot(run_id) is None
