"""HTTP API contract tests (require bootstrap + running PostgreSQL)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_ready():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200


def test_rejected_query_returns_422_with_code():
    with TestClient(app) as client:
        resp = client.post("/api/v1/query-runs", json={"sql": "DROP TABLE customers"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["error"]["code"] == "POLICY_FORBIDDEN_STATEMENT"
        assert body["id"]


def test_invalid_request_returns_400():
    with TestClient(app) as client:
        assert client.post("/api/v1/query-runs", json={}).status_code == 400
        assert client.post("/api/v1/query-runs", json={"sql": 123}).status_code == 400


def test_allowed_query_flow():
    with TestClient(app) as client:
        resp = client.post("/api/v1/query-runs", json={"sql": "SELECT count(*) FROM customers"})
        assert resp.status_code == 202
        run_id = resp.json()["id"]
        assert resp.json()["status"] == "running"

        final = None
        for _ in range(200):
            final = client.get(f"/api/v1/query-runs/{run_id}").json()
            if final["status"] != "running":
                break
            time.sleep(0.1)
        assert final["status"] == "succeeded"
        assert final["row_count"] == 1
        assert final["result"]["columns"][0]["name"] == "count"


def test_unknown_run_returns_404():
    with TestClient(app) as client:
        assert client.get("/api/v1/query-runs/nonexistent-id").status_code == 404
