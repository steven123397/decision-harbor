import json
import os
from pathlib import Path
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.repository import QueryRunRepository
from decisionharbor.worker import QueryWorker


pytestmark = pytest.mark.integration


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
    }
    values.update(overrides)
    return WorkerSettings(**values)


def platform_engine() -> Engine:
    return create_engine(os.environ["PLATFORM_WORKER_DATABASE_URL"], pool_pre_ping=True)


@pytest.fixture(autouse=True)
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


def enqueue(raw_sql: str) -> str:
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.create(raw_sql, "policy-v1", 5_000, 500)
    queued = repository.transition(run.id, "received", status="queued", policy_decision="allowed")
    return queued.id


def attempt_rows(engine: Engine, run_id: str) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, generation, worker_id, lease_expires_at, finished_at
                FROM execution_attempts WHERE run_id = CAST(:run_id AS uuid) ORDER BY generation
                """
            ),
            {"run_id": run_id},
        ).mappings()
        return [dict(row) for row in rows]


def valid_ownership_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM execution_attempts AS attempt
                    JOIN query_runs AS r ON r.id = attempt.run_id
                    WHERE attempt.finished_at IS NULL
                      AND attempt.lease_expires_at > now()
                      AND r.current_attempt_id = attempt.id
                      AND r.status IN ('running', 'cancelling')
                    """
                )
            ).scalar_one()
        )


def test_claim_creates_the_first_attempt_and_moves_the_run_to_running() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    run_id = enqueue("SELECT count(*) FROM customers")

    claimed = worker.claim_next()

    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.generation == 1
    attempts = attempt_rows(engine, run_id)
    assert len(attempts) == 1
    assert attempts[0]["worker_id"] == worker.worker_id
    assert attempts[0]["finished_at"] is None
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "running"
    assert run.started_at is not None
    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT current_attempt_id FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        ).scalar_one()
    assert current == attempts[0]["id"]


def test_claims_follow_queue_order() -> None:
    worker = QueryWorker(worker_settings())
    first = enqueue("SELECT count(*) FROM customers")
    second = enqueue("SELECT count(*) FROM orders")

    claimed_first = worker.claim_next()
    claimed_second = worker.claim_next()

    assert claimed_first is not None and claimed_first.run_id == first
    assert claimed_second is not None and claimed_second.run_id == second


def test_run_once_executes_and_publishes_success_atomically() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    run_id = enqueue("SELECT id FROM customers ORDER BY id LIMIT 2")

    assert worker.run_once() is True

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "succeeded"
    assert run.returned_row_count == 2
    assert run.result_truncated is False
    assert run.finished_at is not None and run.duration_ms is not None
    snapshot = repository.get_result_snapshot(run_id)
    assert snapshot is not None
    assert snapshot.truncated is False
    payload = json.loads(snapshot.payload)
    assert payload["columns"] == [{"name": "id", "type": "bigint"}]
    assert payload["rows"] == [["1"], ["2"]]
    attempts = attempt_rows(engine, run_id)
    assert attempts[0]["finished_at"] is not None


def test_execution_failure_is_published_with_a_stable_error_code() -> None:
    worker = QueryWorker(worker_settings())
    run_id = enqueue("SELECT missing_column FROM customers")

    assert worker.run_once() is True

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "query_semantic_error"
    assert repository.get_result_snapshot(run_id) is None


def test_stale_attempt_cannot_publish_status_or_results() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    run_id = enqueue("SELECT count(*) FROM customers")
    claimed = worker.claim_next()
    assert claimed is not None

    # 模拟接管：另一个所有者创建了 generation+1 的执行尝试并成为当前所有权。
    with engine.begin() as connection:
        new_attempt = connection.execute(
            text(
                """
                INSERT INTO execution_attempts (run_id, generation, worker_id, claimed_at, lease_expires_at)
                VALUES (CAST(:run_id AS uuid), :generation, 'other-worker', now(), now() + interval '15 seconds')
                RETURNING id
                """
            ),
            {"run_id": run_id, "generation": claimed.generation + 1},
        ).scalar_one()
        connection.execute(
            text("UPDATE query_runs SET current_attempt_id = :attempt_id WHERE id = CAST(:run_id AS uuid)"),
            {"attempt_id": new_attempt, "run_id": run_id},
        )

    # 失栅的旧尝试即使租约未过期也不是有效执行所有权，不占用全局容量。
    assert valid_ownership_count(engine) == 1

    result = QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=((1,),),
        truncated=False,
    )
    assert worker.publish_success(claimed, result) is False
    assert worker.publish_failure(claimed, "internal_error", "The query could not be completed.") is False

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "running"
    assert repository.get_result_snapshot(run_id) is None

    # 被栅栏的执行尝试已终结，不阻塞其他运行的领取（容量 2 中只剩 1 个有效所有权）。
    attempts = attempt_rows(engine, run_id)
    assert attempts[0]["finished_at"] is not None
    enqueue("SELECT count(*) FROM order_items")
    two_capacity_worker = QueryWorker(worker_settings(max_concurrency=2))
    reclaimed = two_capacity_worker.claim_next()
    assert reclaimed is not None


def test_global_capacity_of_valid_ownership_blocks_claims() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings(max_concurrency=2))
    first = enqueue("SELECT count(*) FROM customers")
    second = enqueue("SELECT count(*) FROM orders")
    third = enqueue("SELECT count(*) FROM order_items")

    claimed_first = worker.claim_next()
    claimed_second = worker.claim_next()
    assert claimed_first is not None and claimed_second is not None
    assert valid_ownership_count(engine) == 2
    assert worker.claim_next() is None
    assert worker.claim_next() is None

    # 结束一个执行尝试释放有效所有权后，下一个运行可以被领取。
    assert worker.publish_failure(
        claimed_first, "internal_error", "The query could not be completed."
    ) is True
    assert valid_ownership_count(engine) == 1
    reclaimed = worker.claim_next()
    assert reclaimed is not None
    assert reclaimed.run_id == third
    assert reclaimed.generation == 1


def test_lease_renewal_extends_only_fresh_owned_attempts() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings(lease_ms=10_000))
    enqueue("SELECT count(*) FROM customers")
    claimed = worker.claim_next()
    assert claimed is not None

    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT lease_expires_at FROM execution_attempts WHERE id = :attempt_id"),
            {"attempt_id": claimed.attempt_id},
        ).scalar_one()
    worker.renew_leases()
    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT lease_expires_at FROM execution_attempts WHERE id = :attempt_id"),
            {"attempt_id": claimed.attempt_id},
        ).scalar_one()
    assert after > before

    # 已自然过期的所有权不会被心跳复活。
    short_lease_worker = QueryWorker(worker_settings(lease_ms=1))
    enqueue("SELECT count(*) FROM orders")
    short_claimed = short_lease_worker.claim_next()
    assert short_claimed is not None
    time.sleep(0.05)
    short_lease_worker.renew_leases()
    with engine.connect() as connection:
        expired = connection.execute(
            text("SELECT lease_expires_at > now() FROM execution_attempts WHERE id = :attempt_id"),
            {"attempt_id": short_claimed.attempt_id},
        ).scalar_one()
    assert expired is False
