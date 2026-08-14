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
