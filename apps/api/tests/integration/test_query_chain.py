import os

from fastapi.testclient import TestClient
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.config import Settings
from decisionharbor.repository import QueryRunRepository


pytestmark = pytest.mark.integration


def test_real_api_success_rejection_failure_and_audit() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200

        success = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) AS customer_count FROM customers"},
        )
        assert success.status_code == 200
        assert success.json()["data"]["result"]["rows"] == [["100"]]
        success_run = success.json()["data"]["query_run"]
        assert success_run["status"] == "succeeded"
        assert success_run["referenced_objects"] == ["analytics.customers"]

        rejected = client.post(
            "/api/v1/query-runs",
            json={"sql": "DELETE FROM customers"},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "sql_statement_not_allowed"
        assert rejected.json()["data"]["query_run"]["status"] == "rejected"

        failed = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT missing_column FROM customers"},
        )
        assert failed.status_code == 400
        assert failed.json()["error"]["code"] == "query_semantic_error"
        assert "column" not in failed.json()["error"]["message"].lower()

        audit = client.get(f"/api/v1/query-runs/{success_run['id']}")
        assert audit.status_code == 200
        assert set(audit.json()["data"]) == {"query_run"}


def test_real_api_truncates_at_configured_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERY_MAX_ROWS", "2")
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT id FROM orders ORDER BY id"},
        )
    assert response.status_code == 200
    result = response.json()["data"]["result"]
    assert result["rows"] == [["1"], ["2"]]
    assert result["truncated"] is True


def test_real_api_maps_statement_timeout_to_a_safe_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUERY_STATEMENT_TIMEOUT_MS", "1")
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/query-runs",
            json={
                "sql": "SELECT count(*) FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c"
            },
        )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "query_timeout"
    assert response.json()["data"]["query_run"]["status"] == "failed"


def test_startup_recovery_closes_interrupted_audit_records() -> None:
    settings = Settings.from_env()
    repository = QueryRunRepository(settings.platform_database_url)
    received = repository.create("SELECT 1", "policy-v1", 5_000, 500)

    assert repository.recover_interrupted() >= 1

    recovered = repository.get(received.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_code == "execution_interrupted"
