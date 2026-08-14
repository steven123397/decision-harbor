from __future__ import annotations

import os

import pytest


psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.integration


def _url(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip("integration database URLs are not configured")
    return value


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_url(url: str, database: str) -> str:
    prefix, _ = url.rsplit("/", 1)
    return f"{prefix}/{database}"


def test_fixed_dataset_and_runtime_database_boundaries() -> None:
    platform_url = _url("PLATFORM_RUNTIME_URL")
    analytics_url = _url("ANALYTICS_RUNTIME_URL")
    platform_readiness_url = _url("PLATFORM_READINESS_URL")
    analytics_readiness_url = _url("ANALYTICS_READINESS_URL")

    with psycopg.connect(_psycopg_url(analytics_url)) as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM analytics.{table}").fetchone()[0]
            for table in ("customers", "product_categories", "products", "orders", "order_items")
        }
        assert counts == {
            "customers": 100,
            "product_categories": 8,
            "products": 50,
            "orders": 1000,
            "order_items": 3000,
        }
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO analytics.customers "
                "(id, customer_code, display_name, region, created_at) "
                "VALUES (999999, 'forbidden', 'Forbidden', 'North', now())"
            )

    with psycopg.connect(_psycopg_url(platform_url)) as connection:
        assert connection.execute("SELECT count(*) FROM platform.query_runs").fetchone()[0] >= 0

    with psycopg.connect(_psycopg_url(platform_readiness_url)) as connection:
        assert connection.execute("SELECT version_num FROM platform.alembic_version").fetchone()[0] == "platform_0001"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT count(*) FROM platform.query_runs")

    with psycopg.connect(_psycopg_url(analytics_readiness_url)) as connection:
        assert connection.execute("SELECT version_num FROM analytics.alembic_version").fetchone()[0] == "analytics_0001"

    with pytest.raises(psycopg.OperationalError, match="CONNECT privilege"):
        with psycopg.connect(_psycopg_url(_database_url(platform_url, "analytics"))):
            pass

    with pytest.raises(psycopg.OperationalError, match="CONNECT privilege"):
        with psycopg.connect(_psycopg_url(_database_url(analytics_url, "platform"))):
            pass
