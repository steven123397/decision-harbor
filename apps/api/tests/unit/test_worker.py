import pytest
from fastapi.testclient import TestClient

from decisionharbor.worker import create_worker_ready_app, main as worker_main


def test_worker_readiness_endpoints_use_the_unified_envelope() -> None:
    app = create_worker_ready_app(lambda: True)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"data": {"status": "ready"}, "error": None}


def test_worker_readiness_reports_not_ready_safely() -> None:
    app = create_worker_ready_app(lambda: False)
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"
    assert client.get("/health").status_code == 200


def test_worker_main_exits_nonzero_on_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_LEASE_MS", "0")

    with pytest.raises(SystemExit) as caught:
        worker_main()

    assert caught.value.code == 2
