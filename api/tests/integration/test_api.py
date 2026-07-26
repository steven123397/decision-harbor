"""HTTP API 端到端集成测试：端点语义与错误码见 docs/design/query-runs-api.md。

需要已完成迁移与 seed 的真实双库环境；在进程内直接驱动 FastAPI 应用。
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import SYSTEM_CATALOG_BYPASSES

pytestmark = pytest.mark.integration

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready():
    response = client.get("/ready")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"


def test_submit_allowed_query_returns_contract_facts():
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT count(*) AS n FROM orders WHERE status = 'confirmed'"},
    )
    assert response.status_code == 201
    body = response.json()
    run = body["query_run"]
    assert run["status"] == "succeeded"
    assert run["row_count"] == 1
    assert run["truncated"] is False
    assert run["duration_ms"] is not None
    assert run["error"] is None
    assert body["result"]["columns"][0]["name"] == "n"
    # 契约 order_status_counts：confirmed 固定为 720
    assert body["result"]["rows"] == [[720]]


def test_numeric_serialized_as_string():
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT list_price FROM products ORDER BY id LIMIT 1"},
    )
    body = response.json()
    assert body["query_run"]["status"] == "succeeded"
    value = body["result"]["rows"][0][0]
    assert isinstance(value, str) and "." in value


def test_rejected_query_records_and_roundtrips():
    response = client.post("/api/v1/query-runs", json={"sql": "DELETE FROM customers"})
    assert response.status_code == 201
    body = response.json()
    assert body["result"] is None
    run = body["query_run"]
    assert run["status"] == "rejected"
    assert run["error"]["code"] == "policy_forbidden_statement"

    read_back = client.get(f"/api/v1/query-runs/{run['id']}")
    assert read_back.status_code == 200
    stored = read_back.json()["query_run"]
    assert stored["status"] == "rejected"
    assert stored["sql_text"] == "DELETE FROM customers"
    assert stored["error"]["code"] == "policy_forbidden_statement"
    assert "result" not in read_back.json()


def test_multiple_statements_rejected():
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT 1; SELECT 2"}
    )
    assert response.json()["query_run"]["error"]["code"] == "policy_multiple_statements"


def test_forbidden_object_rejected():
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT * FROM pg_catalog.pg_tables"}
    )
    assert response.json()["query_run"]["error"]["code"] == "policy_forbidden_object"


@pytest.mark.parametrize(
    "label,sql,code",
    SYSTEM_CATALOG_BYPASSES,
    ids=[c[0] for c in SYSTEM_CATALOG_BYPASSES],
)
def test_system_catalog_bypass_rejected_without_execution(label, sql, code):
    """已知的系统目录读取路径必须以稳定错误码拒绝，且完全不进入执行。"""
    response = client.post("/api/v1/query-runs", json={"sql": sql})
    assert response.status_code == 201
    body = response.json()
    assert body["result"] is None, f"{label} 返回了结果，说明查询被执行"
    run = body["query_run"]
    assert run["status"] == "rejected"
    assert run["error"]["code"] == code
    # 未执行的证据：审计记录中既无行数也无执行耗时
    assert run["row_count"] is None
    assert run["duration_ms"] is None

    stored = client.get(f"/api/v1/query-runs/{run['id']}").json()["query_run"]
    assert stored["status"] == "rejected"
    assert stored["error"]["code"] == code
    assert stored["sql_text"] == sql


def test_analytical_query_with_cte_and_window_succeeds():
    """收敛函数允许集后，含 CTE、聚合与窗口函数的正常业务查询仍可执行。"""
    sql = (
        "WITH confirmed AS ("
        "SELECT o.customer_id, "
        "oi.quantity * oi.unit_price * (1 - oi.discount_rate) AS amount "
        "FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.status = 'confirmed'"
        ") "
        "SELECT c.region, round(sum(confirmed.amount), 2) AS sales, "
        "rank() OVER (ORDER BY sum(confirmed.amount) DESC) AS rk "
        "FROM confirmed JOIN customers c ON c.id = confirmed.customer_id "
        "GROUP BY c.region ORDER BY rk"
    )
    body = client.post("/api/v1/query-runs", json={"sql": sql}).json()
    run = body["query_run"]
    assert run["status"] == "succeeded", run["error"]
    assert run["row_count"] > 0
    assert [c["name"] for c in body["result"]["columns"]] == ["region", "sales", "rk"]


def test_row_limit_truncation():
    limit = int(os.environ.get("QUERY_ROW_LIMIT", "1000"))
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT id FROM order_items"}
    )
    body = response.json()
    run = body["query_run"]
    assert run["status"] == "succeeded"
    assert run["truncated"] is True
    assert run["row_count"] == limit
    assert len(body["result"]["rows"]) == limit


def test_execution_error_recorded_as_failed():
    response = client.post("/api/v1/query-runs", json={"sql": "SELECT 1/0"})
    body = response.json()
    run = body["query_run"]
    assert run["status"] == "failed"
    assert run["error"]["code"] == "execution_error"
    assert body["result"] is None

    stored = client.get(f"/api/v1/query-runs/{run['id']}").json()["query_run"]
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "execution_error"


def test_get_missing_run_is_404():
    response = client.get("/api/v1/query-runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    response = client.get("/api/v1/query-runs/not-a-uuid")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_invalid_request_body_is_422():
    response = client.post("/api/v1/query-runs", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_oversize_sql_is_422():
    limit = int(os.environ.get("QUERY_MAX_SQL_LENGTH", "100000"))
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT 1 -- " + "x" * limit}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
