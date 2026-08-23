"""取消请求链路与发布竞态：queued/running 取消、幂等、失联收敛与两种竞态顺序。"""

import os
import threading
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.api import create_runtime_app
from decisionharbor.repository import QueryRunRepository
from decisionharbor.worker import QueryWorker

from conftest import platform_engine, worker_settings


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]


def enqueue(raw_sql: str, statement_timeout_ms: int = 5_000) -> str:
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.create(raw_sql, "policy-v1", statement_timeout_ms, 500)
    queued = repository.transition(run.id, "received", status="queued", policy_decision="allowed")
    return queued.id


def attempts(engine: Engine, run_id: str) -> list[dict[str, object]]:
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


def analytics_execution_is_active() -> bool:
    """执行器的命名游标已在 analytics 上真实提取（psycopg3 服务端游标执行期间，
    pg_stat_activity 显示的是 FETCH 语句而非原始 SQL）。"""
    engine = create_engine(os.environ["ANALYTICS_DATABASE_URL"])
    try:
        with engine.connect() as connection:
            active = connection.execute(
                text(
                    """
                    SELECT count(*) FROM pg_stat_activity
                    WHERE state = 'active'
                      AND query LIKE 'FETCH FORWARD%'
                      AND query LIKE '%FROM "query_%'
                      AND pid <> pg_backend_pid()
                      AND backend_type = 'client backend'
                    """
                )
            ).scalar_one()
        return int(active) > 0
    finally:
        engine.dispose()


def wait_until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met before the timeout")


def test_cancel_contract_over_http_for_every_state() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    worker = QueryWorker(worker_settings(), worker_id="contract-worker")

    with TestClient(create_runtime_app()) as client:
        queued_id = enqueue("SELECT count(*) FROM customers")
        cancelled = client.post(f"/api/v1/query-runs/{queued_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["error"] is None
        assert cancelled.json()["data"]["query_run"]["status"] == "cancelled"

        # 终态事实进入审计：取消时间与时长随运行事实一起可读。
        audit = client.get(f"/api/v1/query-runs/{queued_id}").json()["data"]["query_run"]
        assert audit["status"] == "cancelled"
        assert audit["finished_at"] is not None
        assert audit["duration_ms"] is not None

        # queued 取消后不能被 Worker 领取，也不会产生任何执行尝试。
        assert worker.run_once() is False
        assert attempts(engine, queued_id) == []

        # 已 cancelled 的重复取消幂等：返回 200 与既有终态。
        repeat = client.post(f"/api/v1/query-runs/{queued_id}/cancel")
        assert repeat.status_code == 200
        assert repeat.json()["data"]["query_run"]["status"] == "cancelled"
        assert repeat.json()["error"] is None

        # succeeded 运行的迟到取消返回 200 与既有成功事实。
        succeeded_id = enqueue("SELECT count(*) FROM customers")
        assert worker.run_once() is True
        late = client.post(f"/api/v1/query-runs/{succeeded_id}/cancel")
        assert late.status_code == 200
        assert late.json()["data"]["query_run"]["status"] == "succeeded"
        assert repository.get(succeeded_id).status == "succeeded"
        assert repository.get_result_snapshot(succeeded_id) is not None

        # rejected 与 failed 运行不可取消：409 query_run_not_cancellable。
        rejected = client.post("/api/v1/query-runs", json={"sql": "DELETE FROM customers"})
        rejected_id = rejected.json()["data"]["query_run"]["id"]
        failed_id = enqueue("SELECT missing_column FROM customers")
        assert worker.run_once() is True
        for terminal_id in (rejected_id, failed_id):
            response = client.post(f"/api/v1/query-runs/{terminal_id}/cancel")
            assert response.status_code == 409
            assert response.json()["data"] is None
            assert response.json()["error"]["code"] == "query_run_not_cancellable"
            assert response.json()["error"]["query_run_id"] == terminal_id

        missing = client.post("/api/v1/query-runs/11111111-1111-4111-8111-111111111111/cancel")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "query_run_not_found"


def test_running_cancel_persists_intent_then_owner_converges_when_work_ends() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    worker = QueryWorker(worker_settings(lease_ms=60_000), worker_id="cancel-owner")
    run_id = enqueue(
        "SELECT count(*) FROM (SELECT pg_sleep(3)) AS delayed", statement_timeout_ms=8_000
    )
    claimed = worker.claim_next()
    assert claimed is not None

    with TestClient(create_runtime_app()) as client:
        first = client.post(f"/api/v1/query-runs/{run_id}/cancel")
        assert first.status_code == 202
        assert first.json()["data"]["query_run"]["status"] == "cancelling"
        assert first.json()["error"] is None

        # 重复取消幂等：仍返回 202，不制造新状态、错误或执行尝试。
        repeat = client.post(f"/api/v1/query-runs/{run_id}/cancel")
        assert repeat.status_code == 202
        assert repeat.json()["data"]["query_run"]["status"] == "cancelling"
        assert len(attempts(engine, run_id)) == 1

    # 数据库工作自然结束：迟到结果被栅栏丢弃，当前所有者收敛为 cancelled。
    worker.process(claimed)

    run = repository.get(run_id)
    assert run.status == "cancelled"
    assert run.finished_at is not None and run.duration_ms is not None
    assert repository.get_result_snapshot(run_id) is None
    run_attempts = attempts(engine, run_id)
    assert [attempt["generation"] for attempt in run_attempts] == [1]
    assert run_attempts[0]["finished_at"] is not None
    assert run.started_at is not None
    # 取消事实与关联执行尝试可对账：取消时间不早于领取时间。
    assert run.finished_at >= run_attempts[0]["claimed_at"]

    # 取消后的运行不再出现新执行尝试。
    assert worker.run_once() is False
    assert len(attempts(engine, run_id)) == 1


def test_cancel_intent_recorded_before_terminal_publish_discards_late_results() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    worker = QueryWorker(worker_settings(lease_ms=60_000), worker_id="late-cancel-worker")
    run_id = enqueue("SELECT count(*) FROM customers")
    claimed = worker.claim_next()
    assert claimed is not None

    # 数据库工作已完成、终态发布前记录取消意图：取消获胜。
    snapshot = worker._executor.execute(
        claimed.raw_sql, claimed.statement_timeout_ms, claimed.max_rows
    )
    with TestClient(create_runtime_app()) as client:
        response = client.post(f"/api/v1/query-runs/{run_id}/cancel")
        assert response.status_code == 202
        assert response.json()["data"]["query_run"]["status"] == "cancelling"

    # 迟到的成功与失败发布都被取消意图栅栏，不产生效果。
    assert worker.publish_success(claimed, snapshot) is False
    assert (
        worker.publish_failure(claimed, "internal_error", "The query could not be completed.")
        is False
    )
    assert repository.get(run_id).status == "cancelling"

    # 所有者收敛：cancelling 进入 cancelled，结果仍被丢弃。
    assert worker.process(claimed) is None
    run = repository.get(run_id)
    assert run.status == "cancelled"
    assert repository.get_result_snapshot(run_id) is None
    assert all(attempt["finished_at"] is not None for attempt in attempts(engine, run_id))


def test_running_cancel_requests_database_cancellation_best_effort() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    worker = QueryWorker(worker_settings(lease_ms=60_000), worker_id="db-cancel-worker")
    run_id = enqueue(
        "SELECT count(*) FROM (SELECT pg_sleep(20)) AS delayed", statement_timeout_ms=30_000
    )
    claimed = worker.claim_next()
    assert claimed is not None

    executing = threading.Thread(target=worker.process, args=(claimed,))
    executing.start()
    try:
        wait_until(analytics_execution_is_active)

        with TestClient(create_runtime_app()) as client:
            response = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert response.status_code == 202

        # 心跳维护路径发现本人持有的取消意图并 best effort 请求取消数据库工作。
        worker.request_pending_cancellations()

        cancelled_at = time.monotonic()
        executing.join(timeout=15)
        assert not executing.is_alive()

        # 查询被提前中止（远小于 20 秒的 pg_sleep），运行收敛为 cancelled。
        assert time.monotonic() - cancelled_at < 15
        run = repository.get(run_id)
        assert run.status == "cancelled"
        assert repository.get_result_snapshot(run_id) is None
        assert all(attempt["finished_at"] is not None for attempt in attempts(engine, run_id))
    finally:
        executing.join(timeout=30)


def test_failed_database_cancel_keeps_the_intent_and_drops_later_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    worker = QueryWorker(worker_settings(lease_ms=60_000), worker_id="failed-cancel-worker")
    run_id = enqueue(
        "SELECT count(*) FROM (SELECT pg_sleep(3)) AS delayed", statement_timeout_ms=8_000
    )
    claimed = worker.claim_next()
    assert claimed is not None

    def refused_cancel(cancel_key: str) -> bool:
        raise RuntimeError("pg_cancel_backend refused")

    monkeypatch.setattr(worker._executor, "cancel_active", refused_cancel)

    executing = threading.Thread(target=worker.process, args=(claimed,))
    executing.start()
    try:
        wait_until(analytics_execution_is_active)
        outcome, _ = repository.cancel(run_id)
        assert outcome == "cancelling"

        # 数据库取消失败不撤销取消意图，也不让维护路径抛错。
        worker.request_pending_cancellations()
        assert repository.get(run_id).status == "cancelling"

        # 查询 3 秒后自然结束：返回的结果仍被丢弃，取消意图最终获胜。
        executing.join(timeout=15)
        assert not executing.is_alive()
        run = repository.get(run_id)
        assert run.status == "cancelled"
        assert repository.get_result_snapshot(run_id) is None
    finally:
        executing.join(timeout=30)


def test_owner_loss_converges_cancelling_within_one_poll_cycle() -> None:
    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    dying = QueryWorker(worker_settings(lease_ms=1), worker_id="dying-owner")
    takeover = QueryWorker(worker_settings(poll_ms=250), worker_id="takeover-worker")
    run_id = enqueue("SELECT count(*) FROM customers")

    claimed = dying.claim_next()
    assert claimed is not None
    time.sleep(0.05)

    # 所有者失联（租约已过期）后取消意图仍可持久化。
    with TestClient(create_runtime_app()) as client:
        response = client.post(f"/api/v1/query-runs/{run_id}/cancel")
        assert response.status_code == 202
        assert response.json()["data"]["query_run"]["status"] == "cancelling"

    # 接管/清理循环在一个轮询周期内收敛，且不为取消的运行创建新执行尝试。
    assert takeover.run_once() is False

    run = repository.get(run_id)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    run_attempts = attempts(engine, run_id)
    assert [attempt["generation"] for attempt in run_attempts] == [1]
    assert run_attempts[0]["finished_at"] is not None
    assert takeover.run_once() is False
    assert len(attempts(engine, run_id)) == 1


def test_cancelled_run_is_not_bypassed_by_automatic_attempts() -> None:
    from dataclasses import replace

    engine = platform_engine()
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    broken = QueryWorker(
        replace(worker_settings(), analytics_database_url="postgresql+psycopg://analytics_reader:refused@postgres:5433/analytics"),
        worker_id="cancel-racing-worker",
    )
    healthy = QueryWorker(worker_settings(), worker_id="healthy-worker")
    run_id = enqueue("SELECT count(*) FROM customers")

    claimed = broken.claim_next()
    assert claimed is not None
    outcome, _ = repository.cancel(run_id)
    assert outcome == "cancelling"

    broken.process(claimed)

    # 自动尝试释放被取消意图栅栏：不产生新执行尝试，也不会被本人或其他副本领取。
    run_attempts = attempts(engine, run_id)
    assert [attempt["generation"] for attempt in run_attempts] == [1]
    assert healthy.run_once() is False
    assert len(attempts(engine, run_id)) == 1
    run = repository.get(run_id)
    assert run.status == "cancelled"
