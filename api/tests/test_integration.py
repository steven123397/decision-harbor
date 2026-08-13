from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError

from app.bootstrap import seed_analytics
from app.config import load_settings
from app.main import app

pytestmark = pytest.mark.integration


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip("integration database is not configured")
    return value


@pytest.fixture(scope="module")
def settings():
    _require_env("PLATFORM_DATABASE_URL")
    _require_env("ANALYTICS_OWNER_DATABASE_URL")
    _require_env("ANALYTICS_READER_DATABASE_URL")
    return load_settings()


@pytest.fixture(scope="module")
def client(settings):
    with TestClient(app) as test_client:
        ready = test_client.get("/ready")
        assert ready.status_code == 200
        yield test_client


def _swap_database(url: str, database: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + database


def test_platform_identity_cannot_connect_to_analytics(settings) -> None:
    engine = create_engine(_swap_database(settings.platform_database_url, "analytics"))
    with pytest.raises(OperationalError):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    engine.dispose()


def test_analytics_owner_cannot_connect_to_platform(settings) -> None:
    engine = create_engine(_swap_database(settings.analytics_owner_database_url, "platform"))
    with pytest.raises(OperationalError):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    engine.dispose()


def test_analytics_reader_cannot_connect_to_platform_or_write(settings) -> None:
    platform_engine = create_engine(_swap_database(settings.analytics_reader_database_url, "platform"))
    with pytest.raises(OperationalError):
        with platform_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    platform_engine.dispose()

    reader = create_engine(settings.analytics_reader_database_url)
    with reader.connect() as conn:
        write_errors = (ProgrammingError, OperationalError, InternalError)
        with pytest.raises(write_errors):
            conn.execute(text("INSERT INTO analytics.customers (id, customer_code, display_name, region, created_at) VALUES (9999, 'x', 'x', 'East', now())"))
            conn.commit()
        with pytest.raises(write_errors):
            conn.execute(text("UPDATE analytics.products SET name = 'x'"))
            conn.commit()
        with pytest.raises(write_errors):
            conn.execute(text("DELETE FROM analytics.orders"))
            conn.commit()
    reader.dispose()


def test_allowed_query_returns_result_and_audit(client: TestClient, settings) -> None:
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT id, region FROM customers ORDER BY id LIMIT 2"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["row_count"] == 2
    assert [col["name"] for col in body["result"]["columns"]] == ["id", "region"]
    assert body["result"]["rows"][0][0] == 1

    audit = client.get(f"/api/v1/query-runs/{body['id']}")
    assert audit.status_code == 200
    audit_body = audit.json()
    assert audit_body["status"] == "succeeded"
    assert audit_body["result"] is None
    assert audit_body["sql"] == body["sql"]
    assert audit_body["row_count"] == 2

    platform = create_engine(settings.platform_database_url)
    with platform.connect() as conn:
        stored = conn.execute(
            text("SELECT status, row_count FROM query_runs WHERE id = CAST(:id AS uuid)"),
            {"id": body["id"]},
        ).one()
    platform.dispose()
    assert stored[0] == "succeeded"
    assert stored[1] == 2


def test_rejected_query_does_not_succeed(client: TestClient) -> None:
    response = client.post("/api/v1/query-runs", json={"sql": "DELETE FROM orders"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["result"] is None


def test_numeric_values_are_serialized_as_strings(client: TestClient) -> None:
    response = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT list_price FROM products ORDER BY id LIMIT 1"},
    )
    assert response.status_code == 200
    price = response.json()["result"]["rows"][0][0]
    assert isinstance(price, str)


def test_seed_is_idempotent(settings) -> None:
    dataset = Path(settings.dataset_dir)
    owner = create_engine(settings.analytics_owner_database_url)
    seed_analytics(owner, dataset)
    seed_analytics(owner, dataset)
    with owner.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM analytics.customers")).scalar_one()
    owner.dispose()
    assert count == 100
