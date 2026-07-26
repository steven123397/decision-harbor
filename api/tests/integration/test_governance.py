"""双库集成测试：身份分离、API 端到端、就绪语义、seed 幂等。

在 Compose 网络内运行（scripts/test.sh），依赖真实 db 与已完成的迁移/seed。
对应 docs/design/testing.md。
"""

import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, get_settings
from app.db import psycopg_url
from app.main import app
from app.seed import TABLES_IN_LOAD_ORDER, run as run_seed

client = TestClient(app)
settings = get_settings()


# ---- 身份边界 ----


def test_readonly_cannot_insert():
    # 角色默认只读事务或最小授权均可拦截，任一生效即证明写入被拒
    with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
        with pytest.raises(
            (psycopg.errors.ReadOnlySqlTransaction,
             psycopg.errors.InsufficientPrivilege)
        ):
            conn.execute(
                "INSERT INTO analytics.customers "
                "(id, customer_code, display_name, region, created_at) "
                "VALUES (999999, 'X-1', 'X', 'East', now())"
            )


def test_readonly_cannot_ddl():
    with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
        with pytest.raises(
            (psycopg.errors.ReadOnlySqlTransaction,
             psycopg.errors.InsufficientPrivilege)
        ):
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


def test_readonly_role_default_is_readonly():
    """角色默认只读事务（init 脚本设置）。"""
    with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
        row = conn.execute("SHOW transaction_read_only").fetchone()
    assert row[0] == "on"


def test_future_table_not_visible_to_readonly():
    """未来新增的 analytics 表不得自动对查询身份可见。"""
    with psycopg.connect(psycopg_url(settings.analytics_owner_url)) as conn:
        conn.execute("DROP TABLE IF EXISTS analytics.nonbusiness_probe")
        conn.execute("CREATE TABLE analytics.nonbusiness_probe (id int)")
    try:
        with psycopg.connect(psycopg_url(settings.analytics_readonly_url)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM analytics.nonbusiness_probe").fetchall()
    finally:
        with psycopg.connect(psycopg_url(settings.analytics_owner_url)) as conn:
            conn.execute("DROP TABLE IF EXISTS analytics.nonbusiness_probe")


# ---- 契约约束（a0002）----


def _assert_check_violation(sql: str):
    with psycopg.connect(psycopg_url(settings.analytics_owner_url)) as conn:
        try:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(sql)
        finally:
            conn.rollback()


def test_contract_currency_check():
    _assert_check_violation(
        "INSERT INTO analytics.orders (id, order_no, customer_id, ordered_at, status, currency) "
        "VALUES (999999, 'XX-1', 1, now(), 'confirmed', 'USD')"
    )


def test_contract_status_check():
    _assert_check_violation(
        "INSERT INTO analytics.orders (id, order_no, customer_id, ordered_at, status, currency) "
        "VALUES (999999, 'XX-2', 1, now(), 'shipped', 'CNY')"
    )


def test_contract_discount_rate_check():
    _assert_check_violation(
        "INSERT INTO analytics.order_items (id, order_id, product_id, quantity, unit_price, discount_rate) "
        "SELECT 999999, 1, p.id, 1, 1.00, 1.5 FROM analytics.products p "
        "WHERE p.id NOT IN (SELECT product_id FROM analytics.order_items WHERE order_id = 1) LIMIT 1"
    )


def test_contract_cost_not_above_list_check():
    _assert_check_violation(
        "INSERT INTO analytics.products (id, sku, name, category_id, list_price, cost_price, active) "
        "VALUES (999999, 'XX-1', 'X', 1, 10.00, 20.00, true)"
    )


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


def test_cte_shadow_does_not_whitelist_catalog():
    """缺陷回归：同名 CTE 不得让显式 pg_catalog 引用被当作安全。"""
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "WITH pg_tables AS (SELECT 1) SELECT * FROM pg_catalog.pg_tables"},
    )
    record = response.json()["record"]
    assert record["status"] == "rejected"
    assert record["error_code"] == "POLICY_UNAUTHORIZED_OBJECT"


def test_sql_interpreting_function_rejected():
    """缺陷回归：query_to_xml 一类函数不得执行二次 SQL 读取系统对象。"""
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT query_to_xml('SELECT usename FROM pg_user', true, true, '')"},
    )
    record = response.json()["record"]
    assert record["status"] == "rejected"
    assert record["error_code"] == "POLICY_UNSAFE_FUNCTION"


def test_pg_sleep_rejected():
    response = client.post("/api/v1/query-runs", json={"sql": "SELECT pg_sleep(1)"})
    record = response.json()["record"]
    assert record["status"] == "rejected"
    assert record["error_code"] == "POLICY_UNSAFE_FUNCTION"


def test_legit_cte_and_aggregates_still_allowed():
    sql = (
        "WITH regional AS (SELECT region, COUNT(*) AS n FROM customers GROUP BY region) "
        "SELECT region, n FROM regional WHERE n > 10 ORDER BY region"
    )
    response = client.post("/api/v1/query-runs", json={"sql": sql})
    record = response.json()["record"]
    assert record["status"] == "succeeded"
    assert record["row_count"] == 5


def test_multi_statement_rejected():
    response = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT 1; SELECT 2"}
    )
    assert response.json()["record"]["error_code"] == "POLICY_MULTI_STATEMENT"


def test_execution_error_is_failed_with_safe_summary():
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT * FROM customers WHERE id = 'not-a-number'"},
    )
    record = response.json()["record"]
    assert record["status"] == "failed"
    assert record["error_code"] == "EXECUTION_ERROR"
    # 对外只有稳定摘要：含 SQLSTATE，不含数据库原文与数据字面量
    assert "SQLSTATE 22P02" in record["error_message"]
    assert "not-a-number" not in record["error_message"]


def test_statement_timeout_is_failed():
    # 不允许 pg_sleep；用大规模交叉连接制造真实超时（1e10 元组远超 5s）
    response = client.post(
        "/api/v1/query-runs",
        json={
            "sql": "SELECT COUNT(*) FROM customers a CROSS JOIN customers b "
            "CROSS JOIN customers c CROSS JOIN customers d CROSS JOIN customers e"
        },
    )
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


def test_ready_fails_fast_when_db_unreachable(monkeypatch):
    """数据库完全不可达时 /ready 在有限时间内返回失败。"""
    bad = Settings(
        platform_url="postgresql+psycopg://u:p@10.255.255.1:5432/platform",
        analytics_owner_url=None,
        analytics_readonly_url="postgresql+psycopg://u:p@10.255.255.1:5432/analytics",
        statement_timeout_ms=1000,
        max_rows=10,
        datasets_dir="/datasets",
    )
    monkeypatch.setattr(main, "get_settings", lambda: bad)
    start = time.monotonic()
    assert main._ready_check() is False
    assert time.monotonic() - start < 10


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
