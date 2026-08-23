"""提交幂等键的真实数据库集成证据。"""

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

# 幂等记录跨测试运行持久保留（这正是其语义）；键与 SQL 加一次性标记，
# 使本次运行的断言不受历史运行遗留数据影响。键按名字去重（同一用例内
# 重复调用得到同一键），SQL 标记逐次递增使不同用例的基础 SQL 互不计数。
RUN_TOKEN = uuid4().hex[:8]
_KEY_SERIALS: dict[str, int] = {}
_SQL_COUNTER = [0]


def key(name: str) -> str:
    serial = _KEY_SERIALS.setdefault(name, len(_KEY_SERIALS) + 1)
    return f"it-{RUN_TOKEN}-{serial}-{name}"


def tagged(sql: str) -> str:
    # 合法且语义等价的 SQL 变体：run 计数只反映本用例创建的记录。
    _SQL_COUNTER[0] += 1
    return f"{sql} /* {RUN_TOKEN}-{_SQL_COUNTER[0]} */"


def platform_engine() -> Engine:
    return create_engine(os.environ["PLATFORM_DATABASE_URL"], pool_pre_ping=True)


def run_count_by_sql(engine: Engine, raw_sql: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM query_runs WHERE raw_sql = :raw_sql"),
                {"raw_sql": raw_sql},
            ).scalar_one()
        )


def submit(client: TestClient, sql: str, key: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/v1/query-runs", json={"sql": sql}, headers=headers)


def test_replay_with_the_same_key_and_input_returns_the_original_run() -> None:
    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM customers")
    app = create_runtime_app()
    with TestClient(app) as client:
        first = submit(client, sql, key("replay"))
        replay = submit(client, sql, key("replay"))

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["data"]["query_run"]["id"] == first.json()["data"]["query_run"]["id"]
    # 只创建一个查询运行，不产生重复工作。
    assert run_count_by_sql(engine, sql) == 1


def test_replay_of_a_rejected_run_returns_422_with_the_original_rejection() -> None:
    engine = platform_engine()
    sql = tagged("DELETE FROM customers")
    app = create_runtime_app()
    with TestClient(app) as client:
        first = submit(client, sql, key("replay-rejected"))
        replay = submit(client, sql, key("replay-rejected"))

    assert first.status_code == 422
    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == first.json()["error"]["code"]
    assert (
        replay.json()["data"]["query_run"]["id"]
        == first.json()["data"]["query_run"]["id"]
    )
    assert replay.json()["data"]["query_run"]["status"] == "rejected"
    assert run_count_by_sql(engine, sql) == 1


def test_same_key_with_different_input_conflicts() -> None:
    engine = platform_engine()
    conflict_sql = tagged("SELECT count(*) FROM orders")
    app = create_runtime_app()
    with TestClient(app) as client:
        first = submit(client, tagged("SELECT count(*) FROM customers"), key("conflict"))
        conflict = submit(client, conflict_sql, key("conflict"))

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert conflict.json()["data"] is None
    # 冲突请求不创建新运行。
    assert run_count_by_sql(engine, conflict_sql) == 0


def test_missing_key_creates_a_new_run_each_time() -> None:
    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM products")
    app = create_runtime_app()
    with TestClient(app) as client:
        first = submit(client, sql)
        second = submit(client, sql)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["query_run"]["id"] != second.json()["data"]["query_run"]["id"]
    assert run_count_by_sql(engine, sql) == 2


def test_keys_are_scoped_across_the_whole_instance() -> None:
    # 没有用户或租户：同一键在同一 SQL 上跨请求作用域返回同一运行。
    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM order_items")
    app_one = create_runtime_app()
    app_two = create_runtime_app()
    with TestClient(app_one) as client_one, TestClient(app_two) as client_two:
        first = submit(client_one, sql, key("scope"))
        replay = submit(client_two, sql, key("scope"))

    assert first.json()["data"]["query_run"]["id"] == replay.json()["data"]["query_run"]["id"]
    assert run_count_by_sql(engine, sql) == 1


def test_invalid_keys_are_rejected_without_creating_a_run() -> None:
    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM customers")
    app = create_runtime_app()
    with TestClient(app) as client:
        # 空值头部在 HTTP 传输层等同于缺失（客户端剥离），由无键路径覆盖；
        # 其余非法形态必须在创建任何运行前被拒绝。
        for raw_key in ("a" * 129, "key with space", "tab\tkey", b"caf\xe9"):
            response = submit(client, sql, raw_key)

            assert response.status_code == 422, raw_key
            assert response.json()["error"]["code"] == "invalid_idempotency_key", raw_key
            assert response.json()["data"] is None

    assert run_count_by_sql(engine, sql) == 0


def test_replayed_run_is_executed_only_once_by_the_worker() -> None:
    from conftest import worker_settings
    from decisionharbor.worker import QueryWorker

    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM customers")
    worker = QueryWorker(worker_settings())
    app = create_runtime_app()
    with TestClient(app) as client:
        first = submit(client, sql, key("worker-once"))
        replay = submit(client, sql, key("worker-once"))
        run_id = first.json()["data"]["query_run"]["id"]

        assert worker.run_once() is True
        # 重放没有产生新的排队工作。
        assert worker.run_once() is False

    repository = QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])
    run = repository.get(run_id)
    assert run is not None and run.status == "succeeded"
    assert replay.json()["data"]["query_run"]["id"] == run_id


def test_concurrent_submissions_with_the_same_key_create_one_run() -> None:
    engine = platform_engine()
    sql = tagged("SELECT count(*) FROM products")
    app = create_runtime_app()

    def submit_once(_: int):
        with TestClient(app) as client:
            response = submit(client, sql, key("race-same-input"))
            assert response.status_code == 202
            return response.json()["data"]["query_run"]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        run_ids = list(pool.map(submit_once, range(4)))

    # 并发竞态由数据库唯一约束裁决：所有请求看到同一运行，只创建一次。
    assert len(set(run_ids)) == 1
    assert run_count_by_sql(engine, sql) == 1


def test_concurrent_same_key_with_different_sql_settle_on_one_winner() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    sqls = [tagged(f"SELECT count(*) FROM customers WHERE {index} = {index}") for index in range(3)]

    def submit_once(index: int):
        with TestClient(app) as client:
            response = submit(client, sqls[index], key("race-different-input"))
            return response.status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(submit_once, range(3)))

    # 恰好一个请求赢得键，其余返回 409 冲突。
    assert statuses.count(202) == 1
    assert statuses.count(409) == 2
    with engine.connect() as connection:
        created = connection.execute(
            text("SELECT count(*) FROM query_runs WHERE raw_sql = ANY(:sqls)"),
            {"sqls": sqls},
        ).scalar_one()
    assert created == 1
