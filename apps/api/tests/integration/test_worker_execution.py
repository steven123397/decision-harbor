import concurrent.futures
import json
import os
import threading
import time
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import QueryColumn
from decisionharbor.repository import QueryRunRepository
from decisionharbor.snapshots import build_snapshot
from decisionharbor.worker import QueryWorker

from conftest import platform_engine, worker_settings


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]

# 连接必然被拒绝的 analytics 地址：用于稳定构造 analytics_unavailable。
UNREACHABLE_ANALYTICS_URL = "postgresql+psycopg://analytics_reader:refused@postgres:5433/analytics"


def enqueue(raw_sql: str, statement_timeout_ms: int = 5_000) -> str:
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.create(raw_sql, "policy-v1", statement_timeout_ms, 500)
    queued = repository.transition(run.id, "received", status="queued", policy_decision="allowed")
    return queued.id


def attempt_rows(engine: Engine, run_id: str) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, generation, worker_id, claimed_at, lease_expires_at, finished_at
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


def run_ids_with_multiple_valid_ownerships(engine: Engine) -> list[str]:
    """同一运行出现多于一个未过期有效所有权的违例列表，应为空。"""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT CAST(r.id AS text)
                FROM execution_attempts AS attempt
                JOIN query_runs AS r ON r.id = attempt.run_id
                WHERE attempt.finished_at IS NULL
                  AND attempt.lease_expires_at > now()
                  AND r.current_attempt_id = attempt.id
                  AND r.status IN ('running', 'cancelling')
                GROUP BY r.id
                HAVING count(*) > 1
                """
            )
        ).scalars()
        return list(rows)


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

    snapshot = build_snapshot((QueryColumn(name="count", type="bigint"),), ((1,),))
    assert worker.publish_success(claimed, snapshot) is False
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


def test_fresh_lease_cannot_be_taken_over_by_another_worker() -> None:
    engine = platform_engine()
    owner = QueryWorker(worker_settings(lease_ms=60_000), worker_id="owner-worker")
    other = QueryWorker(worker_settings(), worker_id="other-worker")
    run_id = enqueue("SELECT count(*) FROM customers")
    claimed = owner.claim_next()
    assert claimed is not None

    # 租约未过期：其他副本与本人都不能抢占运行中的运行。
    assert other.claim_next() is None
    assert owner.claim_next() is None

    run = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"]).get(run_id)
    assert run is not None and run.status == "running"
    assert valid_ownership_count(engine) == 1


def test_takeover_after_lease_expiry_increments_generation_and_keeps_run_running() -> None:
    engine = platform_engine()
    first_worker = QueryWorker(worker_settings(lease_ms=1), worker_id="first-worker")
    second_worker = QueryWorker(worker_settings(), worker_id="second-worker")
    run_id = enqueue("SELECT count(*) FROM customers")

    first_claim = first_worker.claim_next()
    assert first_claim is not None and first_claim.generation == 1
    with engine.connect() as connection:
        started_at = connection.execute(
            text("SELECT started_at FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        ).scalar_one()

    time.sleep(0.05)

    second_claim = second_worker.claim_next()
    assert second_claim is not None
    assert second_claim.run_id == run_id
    assert second_claim.generation == 2

    # 接管期间查询运行保持 running，不回退 queued；started_at 保留首次执行事实。
    run = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"]).get(run_id)
    assert run is not None and run.status == "running"
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]
    assert attempts[0]["worker_id"] == "first-worker"
    assert attempts[0]["finished_at"] is not None
    assert attempts[1]["worker_id"] == "second-worker"
    assert attempts[1]["finished_at"] is None
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT started_at, current_attempt_id FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        ).one()
    assert row.started_at == started_at
    assert row.current_attempt_id == attempts[1]["id"]
    assert valid_ownership_count(engine) == 1

    snapshot = build_snapshot((QueryColumn(name="count", type="bigint"),), ((3,),))
    assert second_worker.publish_success(second_claim, snapshot) is True
    run = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"]).get(run_id)
    assert run is not None and run.status == "succeeded"


def test_expired_attempt_cannot_publish_even_while_still_current() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings(lease_ms=1), worker_id="expired-worker")
    run_id = enqueue("SELECT count(*) FROM customers")
    claimed = worker.claim_next()
    assert claimed is not None

    time.sleep(0.05)
    # 租约过期但尚无副本接管：失租的当前 generation 也不是可发布所有者。
    snapshot = build_snapshot((QueryColumn(name="count", type="bigint"),), ((1,),))
    assert worker.publish_success(claimed, snapshot) is False
    assert worker.publish_failure(claimed, "internal_error", "The query could not be completed.") is False

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "running"
    assert repository.get_result_snapshot(run_id) is None
    assert valid_ownership_count(engine) == 0

    takeover_worker = QueryWorker(worker_settings(), worker_id="takeover-worker")
    takeover = takeover_worker.claim_next()
    assert takeover is not None
    assert takeover.run_id == run_id and takeover.generation == 2


def test_stale_database_activity_does_not_count_as_ownership_and_cannot_publish() -> None:
    engine = platform_engine()
    stale_worker = QueryWorker(worker_settings(lease_ms=600), worker_id="stale-worker")
    taking_worker = QueryWorker(worker_settings(), worker_id="taking-worker")
    # pg_sleep 返回 void；包一层 count(*) 使结果列是受支持的 bigint。
    run_id = enqueue("SELECT count(*) FROM (SELECT pg_sleep(3)) AS delayed")

    stale_claim = stale_worker.claim_next()
    assert stale_claim is not None and stale_claim.generation == 1

    # 旧副本在 analytics 上真实执行长查询；随后进程"失联"（无心跳续租）。
    stale_thread = threading.Thread(target=stale_worker.process, args=(stale_claim,))
    stale_thread.start()

    time.sleep(1.0)
    # 旧数据库活动尚未物理停止，但已失租，不计为有效执行所有权。
    assert valid_ownership_count(engine) == 0

    takeover_claim = taking_worker.claim_next()
    assert takeover_claim is not None
    assert takeover_claim.run_id == run_id and takeover_claim.generation == 2
    assert valid_ownership_count(engine) == 1
    assert run_ids_with_multiple_valid_ownerships(engine) == []

    taking_worker.process(takeover_claim)
    stale_thread.join(timeout=10)

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "succeeded"
    snapshot = repository.get_result_snapshot(run_id)
    assert snapshot is not None and snapshot.row_count == 1
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT current_attempt_id FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        ).scalar_one()
    assert current == attempts[1]["id"]
    assert valid_ownership_count(engine) == 0


def test_two_worker_replicas_share_the_global_capacity_limit() -> None:
    engine = platform_engine()
    worker_a = QueryWorker(worker_settings(), worker_id="worker-a")
    worker_b = QueryWorker(worker_settings(), worker_id="worker-b")
    enqueued = {enqueue("SELECT count(*) FROM customers") for _ in range(6)}

    claims_a = [worker_a.claim_next() for _ in range(3)]
    claim_b = worker_b.claim_next()
    claimed = [claim for claim in claims_a + [claim_b] if claim is not None]
    assert len(claimed) == 4
    assert len({claim.run_id for claim in claimed}) == 4
    assert all(claim.run_id in enqueued for claim in claimed)
    assert valid_ownership_count(engine) == 4

    # 容量已满：两个副本并发领取都必须落空。
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        exhausted = list(pool.map(lambda fn: fn(), [worker_a.claim_next, worker_b.claim_next]))
    assert exhausted == [None, None]
    assert valid_ownership_count(engine) == 4
    assert run_ids_with_multiple_valid_ownerships(engine) == []

    # 释放一个有效所有权后，另一副本可以领取下一个排队运行。
    assert worker_a.publish_failure(claimed[0], "internal_error", "The query could not be completed.") is True
    assert valid_ownership_count(engine) == 3
    refilled = worker_b.claim_next()
    assert refilled is not None
    assert refilled.run_id in enqueued
    assert refilled.run_id not in {claim.run_id for claim in claimed}

    with engine.connect() as connection:
        attempt_workers = {
            claim.attempt_id: connection.execute(
                text("SELECT worker_id FROM execution_attempts WHERE id = :attempt_id"),
                {"attempt_id": claim.attempt_id},
            ).scalar_one()
            for claim in claims_a + [claim_b, refilled]
            if claim is not None
        }
    assert set(attempt_workers[claim.attempt_id] for claim in claims_a) == {"worker-a"}
    assert attempt_workers[claim_b.attempt_id] == "worker-b"
    assert attempt_workers[refilled.attempt_id] == "worker-b"


def test_heartbeat_keeps_ownership_fresh_during_execution() -> None:
    engine = platform_engine()
    # 执行时长（6 秒）超过租约（5 秒）：只有按 250ms 间隔续租才能保持唯一所有权。
    # 全量测试前的镜像构建可能触发容器 CPU 节流，租约与心跳保留 20 倍裕度。
    worker = QueryWorker(
        worker_settings(lease_ms=5_000, heartbeat_ms=250, poll_ms=50), worker_id="heartbeating-worker"
    )
    run_id = enqueue("SELECT count(*) FROM (SELECT pg_sleep(6)) AS delayed", statement_timeout_ms=10_000)

    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        status = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                status = connection.execute(
                    text("SELECT status FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
                    {"run_id": run_id},
                ).scalar_one()
            if status == "succeeded":
                break
            time.sleep(0.1)
        assert status == "succeeded"
    finally:
        stop.set()
        thread.join(timeout=5)

    # 租约从未在执行期间失效：没有产生接管，也就只有 generation 1 的单一执行尝试。
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1]
    assert attempts[0]["finished_at"] is not None


def test_analytics_unavailable_releases_run_for_automatic_retry() -> None:
    engine = platform_engine()
    broken_worker = QueryWorker(
        replace(worker_settings(), analytics_database_url=UNREACHABLE_ANALYTICS_URL),
        worker_id="broken-worker",
    )
    healthy_worker = QueryWorker(worker_settings(), worker_id="healthy-worker")
    run_id = enqueue("SELECT count(*) FROM customers")

    broken_claim = broken_worker.claim_next()
    assert broken_claim is not None and broken_claim.generation == 1
    broken_worker.process(broken_claim)

    # 自动重试：运行不进入终态，保持 running 等待新执行尝试，错误不落终态字段。
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "running"
    assert run.error_code is None and run.finished_at is None
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1]
    assert attempts[0]["finished_at"] is not None
    assert valid_ownership_count(engine) == 0

    assert healthy_worker.run_once() is True
    run = repository.get(run_id)
    assert run is not None and run.status == "succeeded"
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]

    # 自动尝试不改变 SQL、不重新触发策略判定。
    with engine.connect() as connection:
        persisted = connection.execute(
            text("SELECT raw_sql, policy_decision FROM query_runs WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        ).one()
    assert persisted.raw_sql == "SELECT count(*) FROM customers"
    assert persisted.policy_decision == "allowed"


def test_retryable_failure_exhausts_attempts_into_stable_failed() -> None:
    engine = platform_engine()
    worker = QueryWorker(
        replace(
            worker_settings(max_execution_attempts=2), analytics_database_url=UNREACHABLE_ANALYTICS_URL
        ),
        worker_id="broken-worker",
    )
    run_id = enqueue("SELECT count(*) FROM customers")

    first = worker.claim_next()
    assert first is not None and first.generation == 1
    worker.process(first)
    assert worker.run_once() is True

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "analytics_unavailable"
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    assert worker.claim_next() is None


def test_lease_loss_exhausts_attempts_into_stable_failed() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings(lease_ms=1, max_execution_attempts=2), worker_id="flaky-worker")
    run_id = enqueue("SELECT count(*) FROM customers")

    first = worker.claim_next()
    assert first is not None and first.generation == 1
    time.sleep(0.05)
    second = worker.claim_next()
    assert second is not None and second.generation == 2
    time.sleep(0.05)

    # 第 2 次尝试同样失租：尝试耗尽，收敛为稳定 failed，不再创建第 3 次尝试。
    assert worker.claim_next() is None
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "execution_attempts_exhausted"
    assert run.error_summary is not None
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    assert worker.claim_next() is None


def test_non_retryable_failures_publish_failed_directly() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings(), worker_id="strict-worker")
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    cases = [
        ("SELECT count(*) FROM (SELECT pg_sleep(2)) AS delayed", 300, "query_timeout"),
        ("SELECT missing_column FROM customers", 5_000, "query_semantic_error"),
        ("SELECT repeat('x', 1048577)", 5_000, "result_too_large"),
        ("SELECT '{\"a\":1}'::jsonb", 5_000, "unsupported_result_type"),
    ]
    for raw_sql, statement_timeout_ms, expected_code in cases:
        run_id = enqueue(raw_sql, statement_timeout_ms)

        assert worker.run_once() is True

        run = repository.get(run_id)
        assert run is not None and run.status == "failed", expected_code
        assert run.error_code == expected_code
        attempts = attempt_rows(engine, run_id)
        assert [attempt["generation"] for attempt in attempts] == [1], expected_code
        assert worker.claim_next() is None, expected_code


def test_recovery_when_worker_dies_at_three_points() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    recovery_worker = QueryWorker(worker_settings(), worker_id="recovery-worker")

    # 点 1：领取后、执行前失联。
    claimed_run = enqueue("SELECT count(*) FROM customers")
    dying_worker = QueryWorker(worker_settings(lease_ms=1), worker_id="dying-worker")
    died_claim = dying_worker.claim_next()
    assert died_claim is not None
    time.sleep(0.05)
    assert recovery_worker.run_once() is True
    run = repository.get(claimed_run)
    assert run is not None and run.status == "succeeded"

    # 点 2：查询执行中失联（analytics 上有真实未停止活动）。
    executing_run = enqueue("SELECT count(*) FROM (SELECT pg_sleep(2)) AS delayed")
    mid_worker = QueryWorker(worker_settings(lease_ms=600), worker_id="mid-worker")
    mid_claim = mid_worker.claim_next()
    assert mid_claim is not None
    mid_thread = threading.Thread(target=mid_worker.process, args=(mid_claim,))
    mid_thread.start()
    time.sleep(1.0)
    assert recovery_worker.run_once() is True
    mid_thread.join(timeout=10)
    run = repository.get(executing_run)
    assert run is not None and run.status == "succeeded"
    assert [attempt["generation"] for attempt in attempt_rows(engine, executing_run)] == [1, 2]

    # 点 3：查询执行完成、终态发布前失联：旧 generation 的迟到发布不产生效果。
    pre_publish_run = enqueue("SELECT count(*) FROM customers")
    late_worker = QueryWorker(worker_settings(lease_ms=600), worker_id="late-worker")
    late_claim = late_worker.claim_next()
    assert late_claim is not None
    snapshot = late_worker._executor.execute(
        late_claim.raw_sql, late_claim.statement_timeout_ms, late_claim.max_rows
    )
    time.sleep(1.0)
    assert recovery_worker.run_once() is True
    assert late_worker.publish_success(late_claim, snapshot) is False
    run = repository.get(pre_publish_run)
    assert run is not None and run.status == "succeeded"
    assert [attempt["generation"] for attempt in attempt_rows(engine, pre_publish_run)] == [1, 2]


def test_execution_history_is_reconstructable_from_attempts() -> None:
    engine = platform_engine()
    run_id = enqueue("SELECT count(*) FROM customers")
    first_worker = QueryWorker(worker_settings(lease_ms=1), worker_id="first-owner")
    first_claim = first_worker.claim_next()
    assert first_claim is not None
    time.sleep(0.05)
    second_worker = QueryWorker(worker_settings(), worker_id="second-owner")
    assert second_worker.run_once() is True

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "succeeded"
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1, 2]
    first, second = attempts
    assert first["worker_id"] == "first-owner"
    assert second["worker_id"] == "second-owner"
    assert first["claimed_at"] <= first["finished_at"] <= second["claimed_at"] <= second["finished_at"]
    # 运行的起止事实与首次/末次尝试在同一事务内成对落库，可互相对账。
    assert run.started_at == first["claimed_at"]
    assert run.finished_at == second["finished_at"]


def test_recorded_cancellation_intent_blocks_auto_retry() -> None:
    engine = platform_engine()
    worker = QueryWorker(
        replace(worker_settings(), analytics_database_url=UNREACHABLE_ANALYTICS_URL),
        worker_id="cancel-racing-worker",
    )
    run_id = enqueue("SELECT count(*) FROM customers")
    claimed = worker.claim_next()
    assert claimed is not None

    # 取消意图已持久化（cancel API 在 06 交付；此处直接构造 cancelling 状态）。
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE query_runs SET status = 'cancelling' WHERE id = CAST(:run_id AS uuid)"),
            {"run_id": run_id},
        )

    worker.process(claimed)

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "cancelling"
    attempts = attempt_rows(engine, run_id)
    assert [attempt["generation"] for attempt in attempts] == [1]
    assert attempts[0]["finished_at"] is not None
    assert worker.claim_next() is None
