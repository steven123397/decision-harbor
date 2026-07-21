"""双数据库集成测试：身份分离、端到端查询链路、迁移与 seed 幂等。

需 compose db 启动并由 entrypoint 完成迁移与 seed 后运行。
"""
import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _dsn(user: str, pwd: str, db: str) -> str:
    s = get_settings()
    return f"postgresql+psycopg://{user}:{pwd}@{s.db_host}:{s.db_port}/{db}"


# ---- health / ready ----
def test_health(client):
    assert client.get("/health").status_code == 200


def test_ready(client):
    assert client.get("/ready").status_code == 200


# ---- 身份分离（第二道边界）----
def test_analytics_reader_cannot_write():
    eng = create_engine(
        _dsn(
            get_settings().analytics_reader_user,
            get_settings().analytics_reader_password,
            get_settings().analytics_db,
        )
    )
    with eng.connect() as conn:
        conn.execute(text("SELECT 1 FROM analytics.customers LIMIT 1"))
    with pytest.raises(Exception):
        with eng.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO analytics.customers "
                    "(id, customer_code, display_name, region, created_at) "
                    "VALUES (99999, 'X', 'X', 'East', now())"
                )
            )
    eng.dispose()


def test_analytics_reader_cannot_connect_to_platform():
    s = get_settings()
    eng = create_engine(_dsn(s.analytics_reader_user, s.analytics_reader_password, s.platform_db))
    with pytest.raises(Exception):
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    eng.dispose()


def test_platform_writer_cannot_connect_to_analytics():
    s = get_settings()
    eng = create_engine(_dsn(s.platform_writer_user, s.platform_writer_password, s.analytics_db))
    with pytest.raises(Exception):
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    eng.dispose()


# ---- 端到端：允许 / 拒绝 / 失败 / 查询 ----
def test_post_allowed_query(client):
    r = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT id, customer_code FROM analytics.customers ORDER BY id LIMIT 3"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["row_count"] == 3
    assert len(body["rows"]) == 3
    assert body["columns"][0]["name"] == "id"


def test_post_rejected_delete(client):
    r = client.post("/api/v1/query-runs", json={"sql": "DELETE FROM analytics.customers"})
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "rejected"
    assert body["error_code"] == "FORBIDDEN_STATEMENT"
    assert body["id"] is not None


def test_post_rejected_multi_statement(client):
    r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1; SELECT 2;"})
    assert r.status_code == 422
    assert r.json()["error_code"] == "MULTI_STATEMENT"


def test_post_rejected_system_catalog(client):
    r = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT * FROM pg_catalog.pg_class LIMIT 1"},
    )
    assert r.status_code == 422
    assert r.json()["error_code"] == "FORBIDDEN_OBJECT"


def test_post_row_limit_exceeded(client):
    r = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT * FROM analytics.order_items"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "ROW_LIMIT_EXCEEDED"


def test_get_query_run(client):
    r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1 AS one"})
    rid = r.json()["id"]
    g = client.get(f"/api/v1/query-runs/{rid}")
    assert g.status_code == 200
    body = g.json()
    assert body["id"] == rid
    assert body["status"] == "succeeded"
    assert body["rows"] is None  # GET 不返回结果行


def test_get_query_run_not_found(client):
    g = client.get("/api/v1/query-runs/999999999")
    assert g.status_code == 404


# ---- 迁移与 seed 幂等 ----
def test_seed_counts_match_contract():
    s = get_settings()
    contract = json.loads(
        pathlib.Path(s.analytics_seed_dir, "contract.json").read_text(encoding="utf-8")
    )
    eng = create_engine(
        _dsn(s.analytics_reader_user, s.analytics_reader_password, s.analytics_db)
    )
    with eng.connect() as conn:
        for table, expected in contract["expected_counts"].items():
            actual = conn.execute(
                text(f'SELECT count(*) FROM analytics."{table}"')
            ).scalar()
            assert actual == expected, f"{table}: {actual} != {expected}"
    eng.dispose()


def test_seed_idempotent():
    from app.seed import run as seed_run

    seed_run()  # 重复 seed，必须收敛
    test_seed_counts_match_contract()
