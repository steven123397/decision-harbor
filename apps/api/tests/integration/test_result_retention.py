"""结果保留期、过期语义与幂等清理的真实数据库集成证据。"""

import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.cleanup import ResultRetentionCleaner
from decisionharbor.repository import QueryRunRepository
from decisionharbor.worker import QueryWorker

from conftest import worker_settings


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]


def platform_engine() -> Engine:
    return create_engine(os.environ["PLATFORM_WORKER_DATABASE_URL"], pool_pre_ping=True)


def repository() -> QueryRunRepository:
    return QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])


def run_to_success(worker: QueryWorker, raw_sql: str) -> str:
    run = repository().create(raw_sql, "policy-v1", 5_000, 500)
    repository().transition(run.id, "received", status="queued", policy_decision="allowed")
    assert worker.run_once() is True
    finished = repository().get(run.id)
    assert finished is not None and finished.status == "succeeded"
    return run.id


def backdate_finished_at(engine: Engine, run_id: str, hours: float) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE query_runs
                SET finished_at = now() - (:hours * interval '1 hour'),
                    duration_ms = 0,
                    created_at = now() - (:hours * interval '1 hour')
                WHERE id = CAST(:run_id AS uuid)
                """
            ),
            {"run_id": run_id, "hours": hours},
        )


def attempt_count(engine: Engine, run_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM execution_attempts WHERE run_id = CAST(:run_id AS uuid)"),
                {"run_id": run_id},
            ).scalar_one()
        )


def test_result_is_readable_within_the_retention_window() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    app = create_runtime_app()
    with TestClient(app) as client:
        run_id = run_to_success(worker, "SELECT count(*) FROM customers")
        backdate_finished_at(engine, run_id, hours=23)

        facts = client.get(f"/api/v1/query-runs/{run_id}")
        result = client.get(f"/api/v1/query-runs/{run_id}/result")

    assert facts.status_code == 200
    assert result.status_code == 200
    assert result.json()["data"]["result"]["rows"] == [["100"]]


def test_expired_result_returns_410_and_keeps_full_audit_facts() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    app = create_runtime_app()
    with TestClient(app) as client:
        run_id = run_to_success(worker, "SELECT count(*) FROM orders")
        backdate_finished_at(engine, run_id, hours=25)

        result = client.get(f"/api/v1/query-runs/{run_id}/result")
        facts = client.get(f"/api/v1/query-runs/{run_id}")

    assert result.status_code == 410
    assert result.json()["error"]["code"] == "result_expired"
    assert result.json()["error"]["query_run_id"] == run_id
    # 运行事实与审计事实在过期后仍完整可读。
    assert facts.status_code == 200
    body = facts.json()["data"]["query_run"]
    assert body["status"] == "succeeded"
    assert body["returned_row_count"] == 1
    assert body["raw_sql"] == "SELECT count(*) FROM orders"
    assert body["duration_ms"] == 0


def test_cleanup_removes_only_expired_snapshots_and_keeps_audit_facts() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    # 先清掉历史遗留的过期快照，使删除计数只反映本用例构造的记录。
    ResultRetentionCleaner(engine).cleanup_once()

    fresh_id = run_to_success(worker, "SELECT count(*) FROM customers")
    expired_id = run_to_success(worker, "SELECT count(*) FROM products")
    backdate_finished_at(engine, expired_id, hours=25)

    cleaner = ResultRetentionCleaner(engine)
    removed = cleaner.cleanup_once()

    assert removed == 1
    repo = repository()
    assert repo.get_result_snapshot(fresh_id) is not None
    assert repo.get_result_snapshot(expired_id) is None
    # 查询运行与执行尝试（审计事实）完整保留。
    expired_run = repo.get(expired_id)
    assert expired_run is not None and expired_run.status == "succeeded"
    assert attempt_count(engine, expired_id) == 1


def test_cleanup_is_idempotent_across_repeated_runs() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    # 先清掉历史遗留的过期快照，使删除计数只反映本用例构造的记录。
    ResultRetentionCleaner(engine).cleanup_once()

    expired_id = run_to_success(worker, "SELECT count(*) FROM orders")
    backdate_finished_at(engine, expired_id, hours=30)

    cleaner = ResultRetentionCleaner(engine)
    assert cleaner.cleanup_once() == 1
    # 调度重叠、进程重启后再次执行：均为无操作。
    assert cleaner.cleanup_once() == 0
    assert cleaner.cleanup_once() == 0
    cleaner_after_restart = ResultRetentionCleaner(engine)
    assert cleaner_after_restart.cleanup_once() == 0


def test_concurrent_overlapping_cleanup_runs_leave_consistent_state() -> None:
    from concurrent.futures import ThreadPoolExecutor

    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    # 先清掉历史遗留的过期快照，使删除计数只反映本用例构造的记录。
    ResultRetentionCleaner(engine).cleanup_once()

    expired_ids = [
        run_to_success(worker, "SELECT count(*) FROM orders"),
        run_to_success(worker, "SELECT count(*) FROM products"),
    ]
    for run_id in expired_ids:
        backdate_finished_at(engine, run_id, hours=30)

    # 两个清理例程并发执行（模拟调度重叠）：总删除数恰好等于过期快照数，
    # 不重复删除、不报错、不留下不一致状态。
    with ThreadPoolExecutor(max_workers=2) as pool:
        removed = list(pool.map(lambda _: ResultRetentionCleaner(engine).cleanup_once(), range(2)))

    assert sum(removed) == 2
    repo = repository()
    for run_id in expired_ids:
        assert repo.get_result_snapshot(run_id) is None
        assert repo.get(run_id) is not None
    assert ResultRetentionCleaner(engine).cleanup_once() == 0


def test_cleanup_does_not_touch_unfinished_runs() -> None:
    engine = platform_engine()
    repo = repository()
    run = repo.create("SELECT count(*) FROM customers", "policy-v1", 5_000, 500)
    repo.transition(run.id, "received", status="queued", policy_decision="allowed")

    assert ResultRetentionCleaner(engine).cleanup_once() == 0

    queued = repo.get(run.id)
    assert queued is not None and queued.status == "queued"


def test_expired_and_cleaned_run_reports_410_not_unavailable() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    app = create_runtime_app()
    with TestClient(app) as client:
        run_id = run_to_success(worker, "SELECT count(*) FROM order_items")
        backdate_finished_at(engine, run_id, hours=48)
        assert ResultRetentionCleaner(engine).cleanup_once() == 1

        result = client.get(f"/api/v1/query-runs/{run_id}/result")

    # 快照已被清理删除，但运行事实证明结果曾成功发布：明确过期而非不可用。
    assert result.status_code == 410
    assert result.json()["error"]["code"] == "result_expired"


def test_worker_role_can_delete_only_result_snapshots() -> None:
    engine = platform_engine()
    worker = QueryWorker(worker_settings())
    expired_id = run_to_success(worker, "SELECT count(*) FROM customers")
    backdate_finished_at(engine, expired_id, hours=26)

    # platform_worker 持有的清理例程在真实授权下运行。
    cleaner_engine = create_engine(os.environ["PLATFORM_WORKER_DATABASE_URL"], pool_pre_ping=True)
    assert ResultRetentionCleaner(cleaner_engine).cleanup_once() == 1
    cleaner_engine.dispose()

    repo = repository()
    assert repo.get_result_snapshot(expired_id) is None
    assert repo.get(expired_id) is not None
    assert attempt_count(engine, expired_id) == 1
