"""重试运行的真实数据库集成证据。"""

from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.repository import QueryRunRepository


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]

# 重试幂等记录跨测试运行持久保留（与提交幂等同表同语义）；键加一次性标记，
# 使断言不受历史遗留数据影响。
RUN_TOKEN = uuid4().hex[:8]
_KEY_SERIALS: dict[str, int] = {}


def key(name: str) -> str:
    serial = _KEY_SERIALS.setdefault(name, len(_KEY_SERIALS) + 1)
    return f"it-{RUN_TOKEN}-{serial}-{name}"


def platform_engine() -> Engine:
    return create_engine(os.environ["PLATFORM_DATABASE_URL"], pool_pre_ping=True)


def retry_count_by_source(engine: Engine, source_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM query_runs WHERE retry_of = CAST(:id AS uuid)"),
                {"id": source_id},
            ).scalar_one()
        )


def retry(client: TestClient, run_id: str, idempotency_key: str | None = None):
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return client.post(f"/api/v1/query-runs/{run_id}/retry", headers=headers)


def make_terminal_run(client: TestClient, status: str, sql: str) -> str:
    """经真实链路把一个运行推进到指定终态，返回运行 ID。

    failed 用真实执行语义错误；cancelled 用 queued 取消（链路最短的合法取消）。
    """
    submitted = client.post("/api/v1/query-runs", json={"sql": sql})
    assert submitted.status_code == 202
    run_id = submitted.json()["data"]["query_run"]["id"]
    if status == "failed":
        from decisionharbor.worker import QueryWorker

        from conftest import worker_settings

        worker = QueryWorker(worker_settings())
        assert worker.run_once() is True
    elif status == "cancelled":
        cancelled = client.post(f"/api/v1/query-runs/{run_id}/cancel")
        assert cancelled.status_code == 200
    else:
        raise ValueError(f"unsupported source status: {status}")
    facts = client.get(f"/api/v1/query-runs/{run_id}").json()["data"]["query_run"]
    assert facts["status"] == status, facts
    return run_id


def test_retry_of_a_failed_run_creates_a_new_governed_run() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        source_before = client.get(f"/api/v1/query-runs/{source_id}").json()["data"]["query_run"]

        response = retry(client, source_id)

        assert response.status_code == 202
        new_run = response.json()["data"]["query_run"]
        assert new_run["id"] != source_id
        assert new_run["status"] == "queued"
        assert new_run["retry_of"] == source_id
        assert new_run["raw_sql"] == source_before["raw_sql"]

        # 原始运行的审计事实保持不变。
        source_after = client.get(f"/api/v1/query-runs/{source_id}").json()["data"]["query_run"]
        assert source_after == source_before


def test_retry_run_enters_the_normal_execution_chain() -> None:
    from decisionharbor.worker import QueryWorker

    from conftest import worker_settings

    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        retried = retry(client, source_id)
        new_id = retried.json()["data"]["query_run"]["id"]

        # 重试运行进入正常治理与执行链路：同一语义错误再次以 failed 终态可观察。
        assert worker.run_once() is True
        facts = client.get(f"/api/v1/query-runs/{new_id}").json()["data"]["query_run"]

    assert facts["status"] == "failed"
    assert facts["error_code"] == "query_semantic_error"
    assert facts["retry_of"] == source_id


def test_retried_run_can_be_cancelled_independently() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        new_id = retry(client, source_id).json()["data"]["query_run"]["id"]

        cancelled = client.post(f"/api/v1/query-runs/{new_id}/cancel")

        assert cancelled.status_code == 200
        assert cancelled.json()["data"]["query_run"]["status"] == "cancelled"
        # 取消只影响重试运行：来源事实不变，来源仍可再次重试。
        source = client.get(f"/api/v1/query-runs/{source_id}").json()["data"]["query_run"]
        assert source["status"] == "failed"
        again = retry(client, source_id)
        assert again.status_code == 202


def test_retry_replay_with_the_same_key_returns_the_same_new_run() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")

        first = retry(client, source_id, key("replay"))
        replay = retry(client, source_id, key("replay"))

        assert first.status_code == 202
        assert replay.status_code == 202
        assert replay.json()["data"]["query_run"]["id"] == first.json()["data"]["query_run"]["id"]
        assert replay.json()["data"]["query_run"]["retry_of"] == source_id
        # 幂等重放不重复创建。
        assert retry_count_by_source(engine, source_id) == 1


def test_retry_keys_are_isolated_by_source_run() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_one = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        source_two = make_terminal_run(client, "cancelled", "SELECT count(*) FROM customers")
        shared = key("shared")

        first = retry(client, source_one, shared)
        second = retry(client, source_two, shared)

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["data"]["query_run"]["id"] != second.json()["data"]["query_run"]["id"]
        assert retry_count_by_source(engine, source_one) == 1
        assert retry_count_by_source(engine, source_two) == 1


def test_retry_without_a_key_creates_a_new_run_each_time() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "cancelled", "SELECT count(*) FROM customers")

        first = retry(client, source_id)
        second = retry(client, source_id)

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["data"]["query_run"]["id"] != second.json()["data"]["query_run"]["id"]
        assert retry_count_by_source(engine, source_id) == 2


def test_rejected_and_succeeded_runs_are_not_retryable() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        rejected = client.post("/api/v1/query-runs", json={"sql": "DELETE FROM customers"})
        assert rejected.status_code == 422
        rejected_id = rejected.json()["data"]["query_run"]["id"]

        submitted = client.post("/api/v1/query-runs", json={"sql": "SELECT count(*) FROM customers"})
        succeeded_id = submitted.json()["data"]["query_run"]["id"]

        for run_id in (rejected_id, succeeded_id):
            response = retry(client, run_id)

            assert response.status_code == 409, run_id
            assert response.json()["error"]["code"] == "query_run_not_retryable", run_id
            assert response.json()["error"]["query_run_id"] == run_id
            assert response.json()["data"] is None


def test_non_terminal_runs_are_not_retryable() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        # 非终态来源：queued（未领取）与 running/cancelling 直接经数据库构造。
        submitted = client.post("/api/v1/query-runs", json={"sql": "SELECT count(*) FROM customers"})
        queued_id = submitted.json()["data"]["query_run"]["id"]

        engine = create_engine(os.environ["PLATFORM_WORKER_DATABASE_URL"], pool_pre_ping=True)
        with engine.begin() as connection:
            attempt_id = connection.execute(
                text(
                    "INSERT INTO execution_attempts (run_id, generation, worker_id, claimed_at, lease_expires_at) "
                    "VALUES (CAST(:run_id AS uuid), 1, 'it-worker', now(), now() + interval '10 seconds') "
                    "RETURNING id"
                ),
                {"run_id": queued_id},
            ).scalar_one()
            running_id = str(
                connection.execute(
                    text(
                        "UPDATE query_runs SET status = 'running', started_at = now(), "
                        "current_attempt_id = :attempt_id "
                        "WHERE id = CAST(:run_id AS uuid) RETURNING id"
                    ),
                    {"attempt_id": attempt_id, "run_id": queued_id},
                ).scalar_one()
            )
        engine.dispose()
        assert running_id == queued_id

        for run_id in (queued_id, running_id):
            response = retry(client, run_id)

            assert response.status_code == 409, run_id
            assert response.json()["error"]["code"] == "query_run_not_retryable", run_id

        # running 落到 cancelling 同样不可重试。
        cancelling = client.post(f"/api/v1/query-runs/{running_id}/cancel")
        assert cancelling.status_code == 202
        response = retry(client, running_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "query_run_not_retryable"


def test_retry_of_an_unknown_run_is_not_found() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        response = retry(client, "11111111-1111-4111-8111-111111111111")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "query_run_not_found"


def test_retry_association_is_part_of_the_audit_facts() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        new_id = retry(client, source_id, key("audit")).json()["data"]["query_run"]["id"]

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT retry_of, created_at IS NOT NULL FROM query_runs WHERE id = CAST(:id AS uuid)"
                ),
                {"id": new_id},
            ).one()

    assert str(row[0]) == source_id
    assert row[1] is True


def test_concurrent_retries_with_the_same_key_create_one_new_run() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")

    def retry_once(_: int):
        with TestClient(app) as client:
            response = retry(client, source_id, key("race"))
            assert response.status_code == 202
            return response.json()["data"]["query_run"]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        run_ids = list(pool.map(retry_once, range(4)))

    # 并发竞态由唯一约束裁决：所有请求看到同一新运行，只创建一次。
    assert len(set(run_ids)) == 1
    assert retry_count_by_source(engine, source_id) == 1


def test_cancelled_retry_run_keeps_working_via_a_fresh_retry() -> None:
    """取消不被自动尝试绕过，也不阻断用户显式重试的恢复路径。"""
    from decisionharbor.worker import QueryWorker

    from conftest import worker_settings

    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        cancelled_retry_id = retry(client, source_id).json()["data"]["query_run"]["id"]
        cancelled = client.post(f"/api/v1/query-runs/{cancelled_retry_id}/cancel")
        assert cancelled.status_code == 200

        # 显式重试创建新的独立运行并正常执行。
        recovered_id = retry(client, source_id).json()["data"]["query_run"]["id"]
        assert worker.run_once() is True
        facts = client.get(f"/api/v1/query-runs/{recovered_id}").json()["data"]["query_run"]

    assert facts["status"] == "failed"  # 与来源相同的语义错误
    assert facts["retry_of"] == source_id


def test_retried_sql_still_reaches_the_policy_gate() -> None:
    """治理不可绕过：重试链路与提交共用同一策略判定。"""
    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        # 构造一个 failed 的合法来源，再把其 SQL 改写为策略禁止（模拟数据治理
        # 收紧后同一 SQL 重试）：重试运行必须以新的 rejected 终态被拒绝。
        source_id = make_terminal_run(client, "failed", "SELECT missing_column FROM customers")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE query_runs SET raw_sql = 'DELETE FROM customers' WHERE id = CAST(:id AS uuid)"),
                {"id": source_id},
            )

        response = retry(client, source_id)

    assert response.status_code == 422
    new_run = response.json()["data"]["query_run"]
    assert new_run["status"] == "rejected"
    assert new_run["retry_of"] == source_id
    stored = repository.get(new_run["id"])
    assert stored is not None and stored.status == "rejected"
