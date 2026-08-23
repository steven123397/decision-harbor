"""Worker 接缝集成测试的共享基建：配置、平台引擎与状态收敛。"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.config import WorkerSettings


def worker_settings(**overrides: int) -> WorkerSettings:
    values: dict[str, object] = {
        "platform_database_url": os.environ["PLATFORM_WORKER_DATABASE_URL"],
        "analytics_database_url": os.environ["ANALYTICS_DATABASE_URL"],
        "analytics_readiness_database_url": os.environ["ANALYTICS_READINESS_DATABASE_URL"],
        "dataset_root": Path(os.environ["DATASET_ROOT"]),
        "max_concurrency": 4,
        "lease_ms": 15_000,
        "heartbeat_ms": 3_000,
        "poll_ms": 250,
        "max_execution_attempts": 3,
        "http_port": 8001,
        "cleanup_interval_ms": 300_000,
    }
    values.update(overrides)
    return WorkerSettings(**values)


def platform_engine() -> Engine:
    return create_engine(os.environ["PLATFORM_WORKER_DATABASE_URL"], pool_pre_ping=True)


@pytest.fixture()
def close_leftover_runs():
    """每个用例后收敛遗留状态：未完结尝试会持续占用全局有效所有权容量。"""
    yield
    engine = platform_engine()
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE execution_attempts SET finished_at = now() WHERE finished_at IS NULL")
        )
        connection.execute(
            text(
                """
                UPDATE query_runs
                SET status = 'failed',
                    error_code = 'execution_interrupted',
                    error_summary = 'Execution was interrupted before completion.',
                    finished_at = now(),
                    duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)
                WHERE status IN ('queued', 'running', 'cancelling')
                """
            )
        )
    engine.dispose()
