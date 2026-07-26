"""双数据库身份边界集成测试：验证设计不变量（docs/design/architecture.md）。

需要已完成迁移与 seed 的真实双库环境。
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app import executor

pytestmark = pytest.mark.integration


def _engine_with_database(url_env: str, database: str):
    url = make_url(os.environ[url_env]).set(database=database)
    return create_engine(url, pool_pre_ping=False)


def test_platform_app_cannot_reach_analytics():
    engine = _engine_with_database("PLATFORM_DATABASE_URL", "analytics")
    with pytest.raises(OperationalError):
        with engine.connect():
            pass


def test_analytics_reader_cannot_reach_platform():
    engine = _engine_with_database("ANALYTICS_READER_URL", "platform")
    with pytest.raises(OperationalError):
        with engine.connect():
            pass


def test_analytics_reader_can_read_contract_tables():
    engine = create_engine(os.environ["ANALYTICS_READER_URL"])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM analytics.customers")).scalar_one() == 100


def test_analytics_reader_cannot_write():
    engine = create_engine(os.environ["ANALYTICS_READER_URL"])
    with pytest.raises(DBAPIError) as excinfo:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO analytics.product_categories (id, category_code, name) "
                    "VALUES (999, 'CAT-X', 'x')"
                )
            )
    assert "read-only" in str(excinfo.value).lower()


def test_analytics_reader_cannot_create_objects():
    engine = create_engine(os.environ["ANALYTICS_READER_URL"])
    with pytest.raises((DBAPIError, ProgrammingError)):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE analytics.smuggled (id int)"))


def test_executor_statement_timeout():
    engine = create_engine(os.environ["ANALYTICS_READER_URL"])
    with pytest.raises(executor.ExecutionError) as excinfo:
        executor.execute_readonly(
            engine, "SELECT PG_SLEEP(2)", timeout_ms=300, row_limit=10
        )
    assert excinfo.value.error_code == "execution_timeout"


def test_executor_row_limit_truncates():
    engine = create_engine(os.environ["ANALYTICS_READER_URL"])
    result = executor.execute_readonly(
        engine,
        "SELECT id FROM analytics.order_items ORDER BY id",
        timeout_ms=5000,
        row_limit=100,
    )
    assert result.truncated is True
    assert result.row_count == 100
    assert len(result.rows) == 100
