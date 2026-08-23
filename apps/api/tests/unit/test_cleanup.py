from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.cleanup import ResultRetentionCleaner, is_expired


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_expiry_boundary_is_strict_and_shared_by_read_and_cleanup() -> None:
    finished = NOW - timedelta(hours=24)
    # 恰好到达保留边界：读取不判过期，清理也不删除，两侧口径一致。
    assert is_expired(finished, now=NOW) is False
    assert is_expired(finished - timedelta(microseconds=1), now=NOW) is True
    assert is_expired(finished + timedelta(microseconds=1), now=NOW) is False


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE query_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    finished_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE result_snapshots (
                    run_id TEXT PRIMARY KEY REFERENCES query_runs (id),
                    payload TEXT NOT NULL
                )
                """
            )
        )
    yield engine
    engine.dispose()


def seed_run(connection, run_id: str, status: str, finished_at, payload: str | None) -> None:
    connection.execute(
        text("INSERT INTO query_runs (id, status, finished_at) VALUES (:id, :status, :finished_at)"),
        {"id": run_id, "status": status, "finished_at": finished_at},
    )
    if payload is not None:
        connection.execute(
            text("INSERT INTO result_snapshots (run_id, payload) VALUES (:run_id, :payload)"),
            {"run_id": run_id, "payload": payload},
        )


def cleaner(engine: Engine) -> ResultRetentionCleaner:
    return ResultRetentionCleaner(engine, now=lambda: NOW)


def test_cleanup_deletes_only_expired_snapshots(engine: Engine) -> None:
    with engine.begin() as connection:
        seed_run(connection, "fresh", "succeeded", NOW - timedelta(hours=1), "{}")
        seed_run(connection, "expired", "succeeded", NOW - timedelta(hours=25), "{}")

    removed = cleaner(engine).cleanup_once()

    assert removed == 1
    with engine.connect() as connection:
        remaining = connection.execute(text("SELECT run_id FROM result_snapshots")).scalars().all()
        runs = connection.execute(text("SELECT id, status, finished_at FROM query_runs")).all()
    assert remaining == ["fresh"]
    # 查询运行与审计事实完整保留。
    assert {row.id for row in runs} == {"fresh", "expired"}


def test_cleanup_boundary_at_exactly_24h_is_not_expired(engine: Engine) -> None:
    with engine.begin() as connection:
        seed_run(connection, "edge", "succeeded", NOW - timedelta(hours=24), "{}")

    assert cleaner(engine).cleanup_once() == 0
    with engine.connect() as connection:
        remaining = connection.execute(text("SELECT run_id FROM result_snapshots")).scalars().all()
    assert remaining == ["edge"]


def test_cleanup_is_idempotent_and_repeated_runs_are_no_ops(engine: Engine) -> None:
    with engine.begin() as connection:
        seed_run(connection, "expired", "succeeded", NOW - timedelta(hours=25), "{}")

    runnable = cleaner(engine)
    assert runnable.cleanup_once() == 1
    assert runnable.cleanup_once() == 0
    assert runnable.cleanup_once() == 0


def test_cleanup_never_touches_runs_without_finished_at(engine: Engine) -> None:
    with engine.begin() as connection:
        seed_run(connection, "running", "running", None, None)
        seed_run(connection, "queued", "queued", None, None)

    assert cleaner(engine).cleanup_once() == 0
    with engine.connect() as connection:
        runs = connection.execute(text("SELECT id FROM query_runs")).scalars().all()
    assert set(runs) == {"running", "queued"}
