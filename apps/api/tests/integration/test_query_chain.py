import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from decisionharbor.config import ApiSettings


pytestmark = pytest.mark.integration

TERMINAL_STATUSES = {"rejected", "succeeded", "failed", "cancelled"}


def wait_for_terminal(client: httpx.Client, run_id: str, timeout_seconds: float = 10) -> dict:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = client.get(f"/api/v1/query-runs/{run_id}")
        assert response.status_code == 200
        run = response.json()["data"]["query_run"]
        if run["status"] in TERMINAL_STATUSES:
            return run
        sleep(0.05)
    pytest.fail(f"query run {run_id} did not reach a terminal state")


def live_client() -> httpx.Client:
    return httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=5)


def persisted_run_ids_for_sql(raw_sql: str) -> list[str]:
    engine = create_engine(ApiSettings.from_env().platform_database_url)
    try:
        with engine.connect() as connection:
            return [
                str(run_id)
                for run_id in connection.execute(
                    text("SELECT id FROM query_runs WHERE raw_sql = :raw_sql"),
                    {"raw_sql": raw_sql},
                ).scalars()
            ]
    finally:
        engine.dispose()


def test_live_submission_replays_the_original_run_for_the_same_key_and_sql() -> None:
    idempotency_key = f"sequential-{uuid4()}"
    headers = {"Idempotency-Key": idempotency_key}

    with live_client() as client:
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) FROM customers"},
            headers=headers,
        )
        replay = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) FROM customers"},
            headers=headers,
        )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["data"]["query_run"]["id"] == first.json()["data"]["query_run"]["id"]
    assert replay.json()["error"] is None


def test_live_submission_without_an_idempotency_key_creates_a_new_run_each_time() -> None:
    with live_client() as client:
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) FROM customers"},
        )
        second = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) FROM customers"},
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["data"]["query_run"]["id"] != first.json()["data"]["query_run"]["id"]


def test_live_submission_rejects_reusing_a_key_with_different_sql() -> None:
    idempotency_key = f"conflict-{uuid4()}"
    headers = {"Idempotency-Key": idempotency_key}
    raw_sql = "SELECT count(*) FROM customers"

    with live_client() as client:
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": raw_sql},
            headers=headers,
        )
        conflict = client.post(
            "/api/v1/query-runs",
            json={"sql": raw_sql + " "},
            headers=headers,
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {
        "data": None,
        "error": {
            "code": "idempotency_conflict",
            "message": "Idempotency-Key was already used with a different request.",
        },
    }
    assert "SELECT" not in conflict.text


def test_live_submission_replays_the_original_policy_rejection() -> None:
    idempotency_key = f"rejected-{uuid4()}"
    headers = {"Idempotency-Key": idempotency_key}

    with live_client() as client:
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": "DELETE FROM customers"},
            headers=headers,
        )
        replay = client.post(
            "/api/v1/query-runs",
            json={"sql": "DELETE FROM customers"},
            headers=headers,
        )

    assert first.status_code == 422
    assert replay.status_code == 422
    assert replay.json()["data"]["query_run"]["id"] == first.json()["data"]["query_run"]["id"]
    assert replay.json()["data"]["query_run"]["status"] == "rejected"
    assert replay.json()["error"] == first.json()["error"]


def test_live_concurrent_submission_replays_one_run_for_the_same_key_and_sql() -> None:
    idempotency_key = f"concurrent-{uuid4()}"
    raw_sql = (
        "SELECT "
        + ", ".join(f"count(*) AS total_{index}" for index in range(500))
        + f" FROM customers /* {idempotency_key} */"
    )

    def submit() -> httpx.Response:
        with live_client() as client:
            return client.post(
                "/api/v1/query-runs",
                json={"sql": raw_sql},
                headers={"Idempotency-Key": idempotency_key},
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: submit(), range(8)))

    assert {response.status_code for response in responses} == {202}
    assert {
        response.json()["data"]["query_run"]["id"]
        for response in responses
    } == {responses[0].json()["data"]["query_run"]["id"]}
    assert {
        response.json()["data"]["query_run"]["status"]
        for response in responses
    } <= {"queued", "running", "succeeded"}
    assert {
        response.json()["data"]["query_run"]["policy_decision"]
        for response in responses
    } == {"allowed"}
    assert all(response.json()["error"] is None for response in responses)
    assert persisted_run_ids_for_sql(raw_sql) == [responses[0].json()["data"]["query_run"]["id"]]


def test_live_api_and_worker_complete_allowed_rejected_and_failed_runs() -> None:
    with live_client() as client:
        assert client.get("/ready").status_code == 200

        accepted = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) AS customer_count FROM customers"},
        )
        assert accepted.status_code == 202
        accepted_run = accepted.json()["data"]["query_run"]
        assert accepted_run["status"] == "queued"
        assert "raw_sql" not in accepted_run
        assert set(accepted.json()["data"]) == {"query_run"}

        succeeded = wait_for_terminal(client, accepted_run["id"])
        assert succeeded["status"] == "succeeded"
        assert succeeded["referenced_objects"] == ["analytics.customers"]
        result = client.get(f"/api/v1/query-runs/{succeeded['id']}/result")
        assert result.status_code == 200
        assert result.json()["data"]["result"]["rows"] == [["100"]]

        rejected = client.post(
            "/api/v1/query-runs",
            json={"sql": "DELETE FROM customers"},
        )
        assert rejected.status_code == 422
        rejected_run = rejected.json()["data"]["query_run"]
        assert rejected_run["status"] == "rejected"
        assert "raw_sql" not in rejected_run
        assert rejected.json()["error"]["code"] == "sql_statement_not_allowed"
        rejected_result = client.get(f"/api/v1/query-runs/{rejected_run['id']}/result")
        assert rejected_result.status_code == 409
        assert rejected_result.json()["error"]["code"] == "result_unavailable"

        accepted_failure = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT missing_column FROM customers"},
        )
        assert accepted_failure.status_code == 202
        failed = wait_for_terminal(client, accepted_failure.json()["data"]["query_run"]["id"])
        assert failed["status"] == "failed"
        assert failed["error_code"] == "query_semantic_error"
        assert failed["error_summary"] == "The query is not valid for this dataset."
        assert "column" not in failed["error_summary"].lower()
        assert "raw_sql" not in failed


@pytest.mark.parametrize(
    "raw_sql",
    [
        "SELECT 'maintenance.dataset_seeds'::regclass::text",
        "SELECT 'harbor_admin'::regrole::text",
        "SELECT 'count'::regproc::text",
        "SELECT 'count(integer)'::regprocedure::text",
        "SELECT '='::regoper::text",
        "SELECT '=(integer,integer)'::regoperator::text",
        "SELECT 'pg_catalog'::regnamespace::text",
        "SELECT 'pg_catalog.int4'::regtype::text",
    ],
)
def test_live_api_rejects_catalog_resolving_casts_before_execution(raw_sql: str) -> None:
    with live_client() as client:
        response = client.post("/api/v1/query-runs", json={"sql": raw_sql})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sql_object_not_allowed"
    assert response.json()["data"]["query_run"]["status"] == "rejected"
    assert response.json()["data"]["query_run"]["referenced_objects"] == []


def test_live_worker_preserves_explicit_result_types_and_row_bounds() -> None:
    with live_client() as client:
        accepted = client.post(
            "/api/v1/query-runs",
            json={
                "sql": (
                    "SELECT customer_code::varchar(20), id::bigint, created_at::date "
                    "FROM customers ORDER BY id"
                )
            },
        )
        assert accepted.status_code == 202
        run = wait_for_terminal(client, accepted.json()["data"]["query_run"]["id"])
        assert run["status"] == "succeeded"
        assert run["returned_row_count"] == 100
        result = client.get(f"/api/v1/query-runs/{run['id']}/result").json()["data"]["result"]

    assert [column["type"] for column in result["columns"]] == [
        "character varying",
        "bigint",
        "date",
    ]
    assert result["truncated"] is False


def test_live_worker_maps_statement_timeout_to_a_safe_terminal_failure() -> None:
    with live_client() as client:
        accepted = client.post(
            "/api/v1/query-runs",
            json={
                "sql": "SELECT count(*) FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c"
            },
        )
        assert accepted.status_code == 202
        failed = wait_for_terminal(
            client,
            accepted.json()["data"]["query_run"]["id"],
            timeout_seconds=15,
        )

    assert failed["status"] == "failed"
    assert failed["error_code"] == "query_timeout"
    assert failed["error_summary"] == "The query exceeded its time limit."
