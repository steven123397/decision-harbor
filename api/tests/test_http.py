import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKIP_BOOTSTRAP", "true")
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_ok_without_databases(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_not_ready_before_bootstrap(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_invalid_query_body_is_rejected_without_record(client: TestClient) -> None:
    response = client.post("/api/v1/query-runs", json={"statement": "SELECT 1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REQUEST_INVALID"


def test_unknown_query_run_id_is_not_found(client: TestClient) -> None:
    missing = client.get("/api/v1/query-runs/not-a-uuid")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "QUERY_RUN_NOT_FOUND"


def test_lookup_is_unavailable_before_bootstrap(client: TestClient) -> None:
    unknown = client.get("/api/v1/query-runs/00000000-0000-0000-0000-000000000000")
    assert unknown.status_code == 503
    assert unknown.json() == {"status": "not_ready"}
