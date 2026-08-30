import os
from time import monotonic, sleep
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.domain import TERMINAL_STATUSES


pytestmark = pytest.mark.integration


WAIT_SECONDS = 30
POLL_SECONDS = 0.1

ALLOWED_SQL = "SELECT count(*) AS customer_count FROM customers"
REJECTED_SQL = "SELECT * FROM maintenance.dataset_seeds"

# The retention window is 24 hours from the `finished_at` the database recorded.
RETAINED_HOURS = 23
EXPIRED_HOURS = 25


def platform_admin_url() -> str:
    return os.environ["TEST_ADMIN_DATABASE_URL"].rsplit("/", 1)[0] + "/platform"


def result_url(run_id: str) -> str:
    return f"/api/v1/query-runs/{run_id}/result"


def submit(client: TestClient, raw_sql: str) -> str:
    response = client.post("/api/v1/query-runs", json={"sql": raw_sql})
    return str(response.json()["data"]["query_run"]["id"])


def wait_for_terminal(client: TestClient, run_id: str) -> dict:
    deadline = monotonic() + WAIT_SECONDS
    while monotonic() < deadline:
        run = client.get(f"/api/v1/query-runs/{run_id}").json()["data"]["query_run"]
        if run["status"] in TERMINAL_STATUSES:
            return run
        sleep(POLL_SECONDS)
    raise AssertionError(f"query run {run_id} never reached a terminal state")


def submit_succeeded_run(client: TestClient) -> dict:
    facts = wait_for_terminal(client, submit(client, ALLOWED_SQL))
    assert facts["status"] == "succeeded"
    return facts


def read_snapshot(run_id: str) -> tuple[object, object, bool] | None:
    with psycopg.connect(platform_admin_url()) as connection:
        return connection.execute(
            """
            SELECT result_columns, result_rows, truncated
            FROM query_run_results WHERE query_run_id = %s
            """,
            (run_id,),
        ).fetchone()


def insert_running_run() -> str:
    """A run a worker owns right now: the result is still being produced.

    Neither the submit contract nor the worker can hold this state long enough
    to read a result in it, so the run is written to the audit store directly.
    """
    run_id = str(uuid4())
    with psycopg.connect(platform_admin_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at, started_at,
                execution_attempt_count, attempt_number, attempt_worker_id,
                attempt_generation, lease_expires_at, heartbeat_at
            ) VALUES (
                %s, %s, 'running', 'allowed', 'policy-v1',
                5000, 500, now() - INTERVAL '10 seconds', now() - INTERVAL '8 seconds',
                1, 1, 'worker-test', 1, now() + INTERVAL '15 seconds', now()
            )
            """,
            (run_id, ALLOWED_SQL),
        )
    return run_id


def insert_failed_run() -> str:
    """A run the worker already failed: it finished without a result."""
    run_id = str(uuid4())
    with psycopg.connect(platform_admin_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at, started_at,
                finished_at, duration_ms, error_code, error_summary
            ) VALUES (
                %s, %s, 'failed', 'allowed', 'policy-v1',
                5000, 500, now() - INTERVAL '10 seconds', now() - INTERVAL '8 seconds',
                now() - INTERVAL '3 seconds', 5000,
                'query_timeout', 'The query exceeded its time limit.'
            )
            """,
            (run_id, ALLOWED_SQL),
        )
    return run_id


def backdate_finished_at(run_id: str, hours: float) -> None:
    with psycopg.connect(platform_admin_url()) as connection:
        connection.execute(
            "UPDATE query_runs SET finished_at = now() - %s * INTERVAL '1 hour' WHERE id = %s",
            (hours, run_id),
        )


def test_a_succeeded_run_returns_its_stored_snapshot() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        facts = submit_succeeded_run(client)

        response = client.get(result_url(facts["id"]))

    assert response.status_code == 200
    columns, rows, truncated = read_snapshot(facts["id"])
    assert columns == [{"name": "customer_count", "type": "bigint"}]
    assert rows == [["100"]]
    assert response.json() == {
        "data": {"result": {"columns": columns, "rows": rows, "truncated": truncated}},
        "error": None,
    }


def test_repeated_reads_inside_the_retention_window_return_the_same_snapshot() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        facts = submit_succeeded_run(client)

        first = client.get(result_url(facts["id"]))
        second = client.get(result_url(facts["id"]))
        audit = client.get(f"/api/v1/query-runs/{facts['id']}").json()["data"]["query_run"]

    assert first.json() == second.json()
    # A read never re-runs the SQL: the run still records the single execution
    # attempt that produced the snapshot, and its timing facts are untouched.
    assert audit["execution_attempt_count"] == facts["execution_attempt_count"] == 1
    assert audit["started_at"] == facts["started_at"]
    assert audit["finished_at"] == facts["finished_at"]


def test_a_result_inside_the_retention_window_is_still_readable() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        facts = submit_succeeded_run(client)
        backdate_finished_at(facts["id"], RETAINED_HOURS)

        response = client.get(result_url(facts["id"]))

    assert response.status_code == 200
    assert response.json()["data"]["result"]["rows"] == [["100"]]


def test_a_result_older_than_the_retention_window_is_expired() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        facts = submit_succeeded_run(client)
        assert client.get(result_url(facts["id"])).status_code == 200

        backdate_finished_at(facts["id"], EXPIRED_HOURS)
        expired = client.get(result_url(facts["id"]))
        audit = client.get(f"/api/v1/query-runs/{facts['id']}")

    assert expired.status_code == 410
    error = expired.json()["error"]
    assert error["code"] == "result_expired"
    assert error["query_run_id"] == facts["id"]
    assert set(error) == {"code", "message", "query_run_id"}
    assert audit.status_code == 200
    assert audit.json()["data"]["query_run"]["status"] == "succeeded"


def test_a_run_that_has_not_finished_has_no_result_yet() -> None:
    run_id = insert_running_run()
    app = create_runtime_app()

    with TestClient(app) as client:
        response = client.get(result_url(run_id))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_not_ready"


def test_a_rejected_run_has_no_result_to_read() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        rejected = client.post("/api/v1/query-runs", json={"sql": REJECTED_SQL})
        assert rejected.status_code == 422

        response = client.get(result_url(rejected.json()["data"]["query_run"]["id"]))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_unavailable"


def test_a_failed_run_has_no_result_to_read() -> None:
    run_id = insert_failed_run()
    app = create_runtime_app()

    with TestClient(app) as client:
        response = client.get(result_url(run_id))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_unavailable"


def test_reading_the_result_of_an_unknown_run_reports_it_missing() -> None:
    app = create_runtime_app()

    with TestClient(app) as client:
        response = client.get(result_url(str(uuid4())))

    assert response.status_code == 404
    assert response.json() == {
        "data": None,
        "error": {"code": "query_run_not_found", "message": "Query run was not found."},
    }
