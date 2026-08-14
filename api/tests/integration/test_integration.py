"""Dual-database integration tests (require a running PostgreSQL with bootstrap)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.bootstrap import bootstrap
from app.config import settings
from app.executor import (
    EXEC_ROW_LIMIT_EXCEEDED,
    EXEC_TIMEOUT,
    ExecutionError,
    execute,
)
from app.schema_catalog import load_catalog
from app.service import QueryService, ResultCache
from app.store import QueryRunStore

catalog = load_catalog(Path(settings.dataset_dir) / "contract.json")
EXPECTED_COUNTS = {
    "customers": 100,
    "product_categories": 8,
    "products": 50,
    "orders": 1000,
    "order_items": 3000,
}


def _analytics_counts() -> dict[str, int]:
    engine = create_engine(settings.analytics_admin_dsn, future=True)
    try:
        with engine.connect() as conn:
            return {
                name: conn.execute(
                    text(f'SELECT count(*) FROM "{catalog.schema}"."{name}"')
                ).scalar()
                for name in catalog.tables
            }
    finally:
        engine.dispose()


def test_seed_is_idempotent():
    bootstrap()
    bootstrap()
    assert _analytics_counts() == EXPECTED_COUNTS


def test_platform_writer_persists_audit():
    store = QueryRunStore(settings.platform_writer_dsn)
    run = store.create_running("SELECT 1")
    assert run.id and run.status == "running"
    got = store.get(run.id)
    assert got is not None and got.sql_text == "SELECT 1"


def test_analytics_reader_is_read_only():
    engine = create_engine(settings.analytics_reader_dsn, future=True)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM customers")).scalar() == 100
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "INSERT INTO customers "
                        "(id, customer_code, display_name, region, segment, created_at) "
                        "VALUES (9999, 'X', 'X', 'East', NULL, now())"
                    )
                )
    finally:
        engine.dispose()


def test_executor_returns_rows_and_columns():
    result = execute(
        settings.analytics_reader_dsn,
        "SELECT id, display_name FROM customers LIMIT 3",
        timeout_ms=5000,
        row_limit=10000,
    )
    assert result.row_count == 3
    assert [c.name for c in result.columns] == ["id", "display_name"]


def test_executor_row_limit_exceeded():
    with pytest.raises(ExecutionError) as excinfo:
        execute(settings.analytics_reader_dsn, "SELECT * FROM order_items", 5000, 10)
    assert excinfo.value.code == EXEC_ROW_LIMIT_EXCEEDED


def test_executor_timeout():
    with pytest.raises(ExecutionError) as excinfo:
        execute(settings.analytics_reader_dsn, "SELECT pg_sleep(5)", 500, 10000)
    assert excinfo.value.code == EXEC_TIMEOUT


def test_service_lifecycle_reaches_succeeded():
    store = QueryRunStore(settings.platform_writer_dsn)
    service = QueryService(store, ResultCache(), catalog)
    run = service.submit("SELECT count(*) FROM customers")
    assert run.status == "running"
    final = None
    result = None
    for _ in range(200):
        final, result = service.get(run.id)
        if final.status != "running":
            break
        time.sleep(0.1)
    assert final is not None and final.status == "succeeded"
    assert final.row_count == 1
    assert result is not None and result.row_count == 1


def test_service_rejected_is_persisted():
    store = QueryRunStore(settings.platform_writer_dsn)
    service = QueryService(store, ResultCache(), catalog)
    run = service.submit("DROP TABLE customers")
    assert run.status == "rejected"
    assert run.rejection_code == "POLICY_FORBIDDEN_STATEMENT"


def test_startup_reconciliation_flips_running_to_failed():
    store = QueryRunStore(settings.platform_writer_dsn)
    run = store.create_running("SELECT 1")
    assert store.reconcile_stale_running() >= 1
    got = store.get(run.id)
    assert got.status == "failed"
    assert got.error_code == "EXEC_INTERRUPTED"
