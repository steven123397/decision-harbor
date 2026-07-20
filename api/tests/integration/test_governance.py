"""双库集成测试：身份分离、API 端到端、就绪语义、seed 幂等。

在 Compose 网络内运行（scripts/test.sh），依赖真实 db 与已完成的迁移/seed。
对应 docs/design/testing.md。
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import psycopg_url
from app.main import app
from app.seed import TABLES_IN_LOAD_ORDER, run as run_seed

client = TestClient(app)
settings = get_settings()


# ---- 身份边界 ----


def test_readonly_cannot_insert():
    with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO analytics.customers "
                "(id, customer_code, display_name, region, created_at) "
                "VALUES (999999, 'X-1', 'X', 'East', now())"
            )


def test_readonly_cannot_ddl():
    with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE analytics.forbidden (id int)")


def test_readonly_cannot_reach_platform_db():
    platform_as_readonly = settings.analytics_readonly_url.replace(
        "/analytics", "/platform"
    )
    with pytest.raises(psycopg.Error):
        psycopg.connect(psycopg_url(platform_as_readonly), connect_timeout=3)


def test_platform_app_cannot_reach_analytics_db():
    analytics_as_platform = settings.platform_url.replace("/platform", "/analytics")
    with pytest.raises(psycopg.Error):
        psycopg.connect(psycopg_url(analytics_as_platform), connect_timeout=3)


# ---- API 端到端 ----


def test_allowed_query_succeeds_and_is_audited():
    sql = "SELECT region, COUNT(*) AS n FROM customers GROUP BY region ORDER BY region"
    response = client.post("/api/v1/query-runs", json={"sql": sql})
    assert response.status_code == 200
    body = response.json()
    record = body["record"]
    assert record["status"] == "succeeded"
    assert record["row_count"] == 5
    assert record["duration_ms"] is not None
    assert record["error_code"] is None
    result = body["result"]
    assert [c["name"] for c in result["columns"]] == ["region", "n"]
    assert len(result["rows"]) == 5
    assert result["truncated"] is False

    fetched = client.get(f"/api/v1/query-runs/{record['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["record"]["status"] == "succeeded"
    assert fetched.json()["record"]["sql"] == sql


def test_rejected_query_is_audited():
    response = client.post(
        "/api/v1/query-runs", json={"sql": "DROP TABLE customers"}
    )
    assert response.status_code == 200
    record = response.json()["record"]
    assert record["status"] == "rejected"
    assert record["error_code"] == "POLICY_NON_QUERY_STATEMENT"
    assert record["error_message"]
    assert response.json()["result"] is None

    fetched = client.get(f"/api/v1/query-runs/{record['id']}")
    assert fetched.json()["record"]["error_code"] == "POLICY_NON_QUERY_STATEMENT"


def test_unauthorized_object_rejected():
    response = client.post("/api/v1/query-runs", json={"sql": "SELECT * FROM pg_tables"})
    assert response.json()["record"]["error_code"] == "POLICY_UNAUTHORIZED_OBJECT"


def test_multi_statement_rejected():
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT 1; SELECT 2"}
    )
    assert response.json()["record"]["error_code"] == "POLICY_MULTI_STATEMENT"


def test_execution_error_is_failed():
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT * FROM customers WHERE id = 'not-a-number'"},
    )
    record = response.json()["record"]
    assert record["status"] == "failed"
    assert record["error_code"] == "EXECUTION_ERROR"


def test_statement_timeout_is_failed():
    response = client.post("/api/v1/query-runs", json={"sql": "SELECT pg_sleep(6)"})
    record = response.json()["record"]
    assert record["status"] == "failed"
    assert record["error_code"] == "QUERY_TIMEOUT"


def test_row_limit_truncates():
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT c.id, p.id FROM customers c CROSS JOIN products p"},
    )
    body = response.json()
    assert body["record"]["status"] == "succeeded"
    assert body["result"]["truncated"] is True
    assert body["record"]["row_count"] == settings.max_rows


def test_invalid_request():
    response = client.post("/api/v1/query-runs", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"

    response = client.post("/api/v1/query-runs", json={"sql": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_unknown_record_is_404():
    response = client.get(f"/api/v1/query-runs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RECORD_NOT_FOUND"


def test_health_and_ready():
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


# ---- seed 幂等 ----


def test_seed_is_idempotent():
    first = run_seed()
    second = run_seed()
    assert first == second
    for table in TABLES_IN_LOAD_ORDER:
        count = client.post(
            "/api/v1/query-runs", json={"sql": f"SELECT COUNT(*) FROM {table}"}
        ).json()["record"]["row_count"]
        assert count == 1
