"""Integration tests against a live dual-database stack."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db import Database, reset_db
from app.main import create_app


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        platform_database_url=_env(
            "PLATFORM_DATABASE_URL",
            "postgresql+psycopg://platform_app:platform_app@localhost:5432/platform",
        ),
        analytics_readonly_url=_env(
            "ANALYTICS_READONLY_URL",
            "postgresql+psycopg://analytics_readonly:analytics_readonly@localhost:5432/analytics",
        ),
        analytics_migrator_url=_env(
            "ANALYTICS_MIGRATOR_URL",
            "postgresql+psycopg://analytics_migrator:analytics_migrator@localhost:5432/analytics",
        ),
        statement_timeout_ms=int(_env("STATEMENT_TIMEOUT_MS", "5000")),
        max_result_rows=int(_env("MAX_RESULT_ROWS", "1000")),
    )


@pytest.fixture(scope="module")
def client(settings: Settings) -> TestClient:
    reset_db()
    database = Database(settings)
    app = create_app(settings=settings, database=database)
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_ready(client: TestClient) -> None:
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"


def test_allowed_query_succeeds_and_is_audited(client: TestClient) -> None:
    res = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT id, customer_code FROM customers ORDER BY id LIMIT 3"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["id"]
    assert body["data"]["row_count"] == 3
    assert len(body["data"]["rows"]) == 3

    get_res = client.get(f"/api/v1/query-runs/{body['id']}")
    assert get_res.status_code == 200
    audit = get_res.json()
    assert audit["status"] == "succeeded"
    assert audit["data"]["sql_text"].startswith("SELECT id")
    assert "rows" not in (audit["data"] or {}) or audit["data"].get("rows") is None


def test_write_query_rejected_without_mutating(client: TestClient, settings: Settings) -> None:
    ro = create_engine(settings.analytics_readonly_url)
    with ro.connect() as conn:
        before = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()

    res = client.post(
        "/api/v1/query-runs",
        json={"sql": "DELETE FROM customers WHERE id = 1"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rejected"
    assert body["error"]["code"] == "POLICY_FORBIDDEN_STATEMENT"

    with ro.connect() as conn:
        after = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()
    assert after == before


def test_unauthorized_object_rejected(client: TestClient) -> None:
    res = client.post(
        "/api/v1/query-runs",
        json={"sql": "SELECT * FROM pg_catalog.pg_tables"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rejected"
    assert body["error"]["code"] == "POLICY_FORBIDDEN_OBJECT"


def test_readonly_role_cannot_write(settings: Settings) -> None:
    engine = create_engine(settings.analytics_readonly_url)
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text("DELETE FROM customers WHERE id = 1"))
            conn.commit()


def test_seed_row_counts(settings: Settings) -> None:
    engine = create_engine(settings.analytics_readonly_url)
    expected = {
        "customers": 100,
        "product_categories": 8,
        "products": 50,
        "orders": 1000,
        "order_items": 3000,
    }
    with engine.connect() as conn:
        for table, count in expected.items():
            actual = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert actual == count, table


def test_row_limit_failure(client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a low max via a dedicated app instance
    tight = Settings(
        platform_database_url=settings.platform_database_url,
        analytics_readonly_url=settings.analytics_readonly_url,
        analytics_migrator_url=settings.analytics_migrator_url,
        max_result_rows=2,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    reset_db()
    app = create_app(settings=tight, database=Database(tight))
    with TestClient(app) as c:
        res = c.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT id FROM customers ORDER BY id"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "EXECUTION_ROW_LIMIT"
