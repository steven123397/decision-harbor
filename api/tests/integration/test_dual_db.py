"""双数据库集成测试：身份分离、seed 幂等、端到端链路。

在 api 容器内运行（make integration-test），依赖 compose 栈已完成引导。
"""

from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

OWNER_DSN = os.environ["ANALYTICS_OWNER_DSN"]
READONLY_DSN = os.environ["ANALYTICS_READONLY_DSN"]
PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]


def test_readonly_identity_cannot_write():
    # InsufficientPrivilege（无表权限）或 ReadOnlySqlTransaction（角色级
    # default_transaction_read_only 兜底）都证明写被数据库层拒绝。
    with psycopg.connect(READONLY_DSN) as conn:
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analytics.customers")


def test_readonly_identity_cannot_ddl():
    with psycopg.connect(READONLY_DSN) as conn:
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE analytics.hack (id int)")


def test_readonly_identity_cannot_connect_platform():
    platform_dsn = READONLY_DSN.rsplit("/", 1)[0] + "/platform"
    with pytest.raises(psycopg.Error):
        with psycopg.connect(platform_dsn, connect_timeout=5):
            pass


def test_analytics_allows_only_contract_schema():
    with psycopg.connect(READONLY_DSN) as conn, conn.cursor() as cur:
        # public schema 中无授权对象可访问
        with pytest.raises(psycopg.errors.UndefinedTable):
            cur.execute("SELECT * FROM public.something")


def test_readonly_can_select_contract_tables():
    with psycopg.connect(READONLY_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.customers")
        assert cur.fetchone()[0] == 100


def test_platform_app_can_read_write_audit():
    engine = create_engine(PLATFORM_APP_URL)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM query_runs")).scalar()
        assert isinstance(n, int)
    engine.dispose()


def test_seed_counts_match_contract():
    expected = {
        "customers": 100,
        "product_categories": 8,
        "products": 50,
        "orders": 1000,
        "order_items": 3000,
    }
    with psycopg.connect(READONLY_DSN) as conn, conn.cursor() as cur:
        for table, want in expected.items():
            cur.execute(f'SELECT count(*) FROM analytics."{table}"')
            assert cur.fetchone()[0] == want, table


def test_contract_check_constraints_enforced():
    """契约约束已进入数据库：越界数据被 CHECK 拒绝。"""
    with psycopg.connect(OWNER_DSN) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics.order_items "
                    "(id, order_id, product_id, quantity, unit_price, discount_rate) "
                    "VALUES (990001, 1, 1, 1, 1.00, 1.5)"
                )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics.customers "
                    "(id, customer_code, display_name, region, created_at) "
                    "VALUES (990002, 'X-1', 'X', 'Mars', now())"
                )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics.products "
                    "(id, sku, name, category_id, list_price, cost_price, active) "
                    "VALUES (990003, 'SKU-X', 'X', 1, 10.00, 11.00, true)"
                )
        conn.rollback()


# 已知治理绕过路径：任何一条都必须在策略层被拒绝。
BYPASS_VECTORS = [
    # CTE 名遮蔽 + 非递归自引用回退到系统目录
    "WITH pg_class AS (SELECT relname FROM pg_class) SELECT * FROM pg_class",
    # 对象标识类型转换
    "SELECT 'pg_catalog.pg_class'::regclass",
    # 函数逃逸路径
    "SELECT query_to_xml('SELECT relname FROM pg_class', true, true, '')",
    # 系统信息函数
    "SELECT current_user",
]


def test_end_to_end_allow_and_reject_and_fetch():
    from app.main import create_app

    # with 语句触发 lifespan，注入 app.state.runs。
    with TestClient(create_app()) as client:

        ok = client.post(
            "/api/v1/query-runs", json={"sql": "SELECT region FROM customers ORDER BY region"}
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["outcome"] == "succeeded"
        assert body["result"]["row_count"] == 100
        run_id = body["run"]["id"]

        fetched = client.get(f"/api/v1/query-runs/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["run"]["state"] == "succeeded"

        bad = client.post("/api/v1/query-runs", json={"sql": "DROP TABLE customers"})
        assert bad.status_code == 200
        assert bad.json()["outcome"] == "rejected"
        assert bad.json()["run"]["rejection_code"] == "QY_FORBIDDEN_STATEMENT"

        missing = client.get("/api/v1/query-runs/999999")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "QY_RUN_NOT_FOUND"


def test_end_to_end_governance_bypass_vectors_rejected():
    from app.main import create_app

    with TestClient(create_app()) as client:
        for sql in BYPASS_VECTORS:
            resp = client.post("/api/v1/query-runs", json={"sql": sql})
            assert resp.status_code == 200, sql
            body = resp.json()
            assert body["outcome"] == "rejected", sql
            assert body["run"]["rejection_code"] in {
                "QY_UNAUTHORIZED_OBJECT",
                "QY_FORBIDDEN_FUNCTION",
            }, sql
            assert "pg_class" not in str(body.get("result")), sql


def test_end_to_end_failed_semantics():
    from app.main import create_app

    with TestClient(create_app()) as client:
        # 策略通过但列不存在：执行失败面板语义（failed + error_code）
        resp = client.post(
            "/api/v1/query-runs", json={"sql": "SELECT no_such_column FROM customers"}
        )
        body = resp.json()
        assert body["outcome"] == "failed"
        assert body["run"]["error_code"] == "QY_EXECUTION_ERROR"
        assert body["run"]["rejection_code"] is None

        # 非整数 id 一律 404
        missing = client.get("/api/v1/query-runs/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "QY_RUN_NOT_FOUND"
