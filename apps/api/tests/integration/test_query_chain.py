import os
from time import monotonic

from fastapi.testclient import TestClient
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.config import Settings
from decisionharbor.repository import QueryRunRepository


pytestmark = pytest.mark.integration


def test_api_is_ready_without_analytics_execution_credentials() -> None:
    assert "ANALYTICS_DATABASE_URL" not in os.environ
    app = create_runtime_app()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
        started_at = monotonic()
        ready = client.get("/ready")

    assert monotonic() - started_at < 1.0
    assert ready.status_code == 200
    assert ready.json() == {"data": {"status": "ready"}, "error": None}


def test_allowed_query_is_queued_without_returning_a_result() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        submitted = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) AS customer_count FROM customers"},
        )

        assert submitted.status_code == 202
        payload = submitted.json()
        assert payload["error"] is None
        run = payload["data"]["query_run"]
        assert run["status"] == "queued"
        assert run["referenced_objects"] == ["analytics.customers"]
        assert run["execution_attempt_count"] == 0
        assert set(payload["data"]) == {"query_run"}

        audit = client.get(f"/api/v1/query-runs/{run['id']}")

    assert audit.status_code == 200
    assert audit.json()["data"]["query_run"]["status"] == "queued"


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


def test_startup_recovery_closes_interrupted_audit_records() -> None:
    settings = Settings.from_env()
    repository = QueryRunRepository(settings.platform_database_url)
    received = repository.create("SELECT 1", "policy-v1", 5_000, 500)

    assert repository.recover_interrupted() >= 1

    recovered = repository.get(received.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_code == "execution_interrupted"
