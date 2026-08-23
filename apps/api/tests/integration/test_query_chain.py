from fastapi.testclient import TestClient
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.worker import QueryWorker

from conftest import worker_settings


pytestmark = pytest.mark.integration


def submit(client: TestClient, sql: str):
    response = client.post("/api/v1/query-runs", json={"sql": sql})
    assert response.status_code == 202
    run = response.json()["data"]["query_run"]
    assert run["status"] == "queued"
    not_ready = client.get(f"/api/v1/query-runs/{run['id']}/result")
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "result_not_ready"
    return run


def test_real_api_success_rejection_failure_and_audit() -> None:
    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200

        success_run = submit(client, "SELECT count(*) AS customer_count FROM customers")
        assert worker.run_once() is True

        success = client.get(f"/api/v1/query-runs/{success_run['id']}/result")
        assert success.status_code == 200
        assert success.json()["data"]["result"]["rows"] == [["100"]]
        facts = client.get(f"/api/v1/query-runs/{success_run['id']}").json()["data"]["query_run"]
        assert facts["status"] == "succeeded"
        assert facts["referenced_objects"] == ["analytics.customers"]
        assert facts["returned_row_count"] == 1

        casts_run = submit(
            client,
            "SELECT customer_code::varchar(20), id::bigint, created_at::date "
            "FROM customers ORDER BY id LIMIT 1",
        )
        assert worker.run_once() is True
        casts = client.get(f"/api/v1/query-runs/{casts_run['id']}/result")
        assert casts.status_code == 200
        assert [column["type"] for column in casts.json()["data"]["result"]["columns"]] == [
            "character varying",
            "bigint",
            "date",
        ]

        rejected = client.post(
            "/api/v1/query-runs",
            json={"sql": "DELETE FROM customers"},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "sql_statement_not_allowed"
        assert rejected.json()["data"]["query_run"]["status"] == "rejected"

        failed_run = submit(client, "SELECT missing_column FROM customers")
        assert worker.run_once() is True
        failed = client.get(f"/api/v1/query-runs/{failed_run['id']}").json()["data"]["query_run"]
        assert failed["status"] == "failed"
        assert failed["error_code"] == "query_semantic_error"
        unavailable = client.get(f"/api/v1/query-runs/{failed_run['id']}/result")
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "result_unavailable"

        audit = client.get(f"/api/v1/query-runs/{success_run['id']}")
        assert audit.status_code == 200
        assert set(audit.json()["data"]) == {"query_run"}


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
def test_real_api_rejects_catalog_resolving_casts_before_execution(raw_sql: str) -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/query-runs", json={"sql": raw_sql})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sql_object_not_allowed"
    assert response.json()["data"]["query_run"]["status"] == "rejected"
    assert response.json()["data"]["query_run"]["referenced_objects"] == []


def test_real_api_truncates_at_configured_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERY_MAX_ROWS", "2")
    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        run = submit(client, "SELECT id FROM orders ORDER BY id")
        assert worker.run_once() is True

        facts = client.get(f"/api/v1/query-runs/{run['id']}").json()["data"]["query_run"]
        result = client.get(f"/api/v1/query-runs/{run['id']}/result").json()["data"]["result"]

    assert facts["status"] == "succeeded"
    assert result["rows"] == [["1"], ["2"]]
    assert result["truncated"] is True


def test_real_api_maps_statement_timeout_to_a_safe_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUERY_STATEMENT_TIMEOUT_MS", "1")
    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        run = submit(
            client,
            "SELECT count(*) FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c",
        )
        assert worker.run_once() is True

        facts = client.get(f"/api/v1/query-runs/{run['id']}").json()["data"]["query_run"]

    assert facts["status"] == "failed"
    assert facts["error_code"] == "query_timeout"


def test_api_startup_leaves_unfinished_runs_untouched() -> None:
    worker = QueryWorker(worker_settings())
    app = create_runtime_app()
    with TestClient(app) as client:
        queued = submit(client, "SELECT count(*) FROM customers")

        # 重新创建应用（模拟进程重启）不会把 queued 运行批量收敛为失败。
        with TestClient(create_runtime_app()) as restarted:
            facts = restarted.get(f"/api/v1/query-runs/{queued['id']}").json()["data"]["query_run"]
        assert facts["status"] == "queued"

        assert worker.run_once() is True
        done = client.get(f"/api/v1/query-runs/{queued['id']}").json()["data"]["query_run"]
    assert done["status"] == "succeeded"
