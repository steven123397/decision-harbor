"""路由层错误语义：非法请求体返回 400 + 稳定错误码（design/api.md）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    # 不触发 lifespan（不连数据库）；本文件只测请求体校验。
    return TestClient(create_app(), raise_server_exceptions=False)


def test_non_string_sql_returns_400_with_stable_code(client):
    r = client.post("/api/v1/query-runs", json={"sql": 123})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "QY_INVALID_REQUEST"


def test_missing_sql_returns_400(client):
    r = client.post("/api/v1/query-runs", json={"unexpected": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_extra_fields_rejected(client):
    r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1", "extra": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_idempotency_key_of_wrong_type_returns_400(client):
    r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1", "idempotency_key": 7})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_empty_idempotency_key_returns_400(client):
    r = client.post("/api/v1/query-runs", json={"sql": "SELECT 1", "idempotency_key": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_overlong_idempotency_key_returns_400(client):
    r = client.post(
        "/api/v1/query-runs", json={"sql": "SELECT 1", "idempotency_key": "k" * 129}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


@pytest.mark.parametrize("limit", ["abc", "0", "-1", "101", "1.5"])
def test_invalid_limit_returns_400(client, limit):
    r = client.get(f"/api/v1/query-runs?limit={limit}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


@pytest.mark.parametrize(
    "cursor",
    ["!!!not-base64!!!", "eHl6", "MTIz", ""],  # 非法字符 / 无分隔符 / 纯数字 / 空串
)
def test_malformed_cursor_returns_400(client, cursor):
    r = client.get(f"/api/v1/query-runs?cursor={cursor}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_valid_base64_but_invalid_timestamp_returns_400(client):
    import base64

    bogus = base64.urlsafe_b64encode("not-a-time|5".encode()).decode()
    r = client.get(f"/api/v1/query-runs?cursor={bogus}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


def test_non_object_body_returns_400(client):
    r = client.post(
        "/api/v1/query-runs", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "QY_INVALID_REQUEST"


@pytest.mark.parametrize("run_id", ["does-not-exist", "1.5", "abc"])
def test_non_integer_run_id_returns_404_not_400(client, run_id):
    """非整数路径是「记录不存在」，不能被请求体校验处理器误映射成 400。"""
    r = client.get(f"/api/v1/query-runs/{run_id}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "QY_RUN_NOT_FOUND"
