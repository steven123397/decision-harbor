"""Integration tests requiring a running Compose stack (docker compose up -d)."""
import os

import pytest
import httpx
from sqlalchemy import create_engine, text

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
PLATFORM_URL = os.environ.get(
    "PLATFORM_DATABASE_URL",
    "postgresql+psycopg://platform_app:platform_pass@localhost:15432/platform",
)
ANALYTICS_READER_URL = os.environ.get(
    "ANALYTICS_DATABASE_URL",
    "postgresql+psycopg://analytics_reader:reader_pass@localhost:15432/analytics",
)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API_BASE, timeout=35) as c:
        yield c


class TestHealthEndpoints:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ready(self, client):
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestQueryRunLifecycle:
    def test_allowed_query_returns_results(self, client):
        r = client.post("/api/v1/query-runs", json={"sql": "SELECT COUNT(*) AS total FROM customers"})
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["data"]["row_count"] == 1
        assert body["data"]["rows"][0][0] == 100
        assert body["data"]["columns"][0]["name"] == "total"

    def test_rejected_query_returns_error_code(self, client):
        r = client.post("/api/v1/query-runs", json={"sql": "DROP TABLE customers"})
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "rejected"
        assert body["error"]["code"] == "FORBIDDEN_STATEMENT"

    def test_audit_record_persisted(self, client):
        r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1 AS one"})
        run_id = r.json()["data"]["id"]
        r2 = client.get(f"/api/v1/query-runs/{run_id}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "succeeded"

    def test_not_found(self, client):
        r = client.get("/api/v1/query-runs/999999")
        assert r.status_code == 404

    def test_invalid_body(self, client):
        r = client.post("/api/v1/query-runs", json={"wrong": "field"})
        assert r.status_code == 422


class TestDatabaseIsolation:
    def test_analytics_reader_can_select(self):
        engine = create_engine(ANALYTICS_READER_URL)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM customers"))
            assert result.scalar() == 100

    def test_analytics_reader_cannot_insert(self):
        engine = create_engine(ANALYTICS_READER_URL)
        with pytest.raises(Exception, match="permission denied"):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO customers (id, customer_code, display_name, region, created_at) VALUES (9999, 'X', 'X', 'East', now())"))

    def test_platform_app_cannot_access_analytics(self):
        engine = create_engine(PLATFORM_URL)
        with pytest.raises(Exception):
            with engine.connect() as conn:
                conn.execute(text("SELECT COUNT(*) FROM customers"))
