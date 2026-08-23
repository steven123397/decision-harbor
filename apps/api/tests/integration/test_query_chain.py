import os
from time import monotonic, sleep

import httpx
import pytest


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
