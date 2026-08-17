"""提交幂等与历史分页集成测试（#9）：重放 / 冲突 / 并发同键 / 游标分页。

在 api-test 容器内运行，走进程内 ASGI + 真实 platform 库；worker 排水
开关保证 queued 运行不被执行，状态可确定性断言（ADR-0018 状态码合同）。
键每次生成唯一值：集成库跨测试运行持久存在，不能依赖空表。
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_platform_engine, create_platform_session_factory
from app.main import create_app
from app.runs import queue

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def _count_runs_with_key(sessions, key: str) -> int:
    with sessions() as s:
        return s.execute(
            text("SELECT count(*) FROM query_runs WHERE idempotency_key = :k"),
            {"k": key},
        ).scalar_one()


def test_same_key_same_sql_replays_original_run():
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    key = f"replay-{uuid.uuid4().hex}"
    try:
        with TestClient(create_app()) as client:
            sql = "SELECT 1 AS x"
            first = client.post(
                "/api/v1/query-runs", json={"sql": sql, "idempotency_key": key}
            )
            assert first.status_code == 202
            original = first.json()["run"]
            assert original["idempotency_key"] == key
            assert original["attempt"] == 1

            replay = client.post(
                "/api/v1/query-runs", json={"sql": sql, "idempotency_key": key}
            )
            assert replay.status_code == 200
            assert replay.json()["run"] == original

            assert _count_runs_with_key(sessions, key) == 1
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_same_key_different_sql_returns_409_conflict():
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    key = f"conflict-{uuid.uuid4().hex}"
    try:
        with TestClient(create_app()) as client:
            first = client.post(
                "/api/v1/query-runs",
                json={"sql": "SELECT 1 AS x", "idempotency_key": key},
            )
            assert first.status_code == 202

            second = client.post(
                "/api/v1/query-runs",
                json={"sql": "SELECT 2 AS y", "idempotency_key": key},
            )
            assert second.status_code == 409
            detail = second.json()["detail"]
            assert detail["code"] == "idempotency_conflict"
            assert detail["message"]

            assert _count_runs_with_key(sessions, key) == 1
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_replay_of_rejected_run_returns_original():
    """被拒绝的原运行同样按重放返回：键绑定的是记录，不是任务。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    key = f"rejected-{uuid.uuid4().hex}"
    try:
        with TestClient(create_app()) as client:
            sql = "DELETE FROM customers"
            first = client.post(
                "/api/v1/query-runs", json={"sql": sql, "idempotency_key": key}
            )
            assert first.status_code == 422
            original = first.json()["run"]
            assert original["state"] == "rejected"
            assert original["rejection_code"]

            replay = client.post(
                "/api/v1/query-runs", json={"sql": sql, "idempotency_key": key}
            )
            assert replay.status_code == 200
            assert replay.json()["run"] == original

            assert _count_runs_with_key(sessions, key) == 1
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_concurrent_same_key_submissions_yield_single_run():
    """并发同键提交由唯一索引裁决：恰好一个 202，其余重放 200，同一 id。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    key = f"race-{uuid.uuid4().hex}"
    try:
        with TestClient(create_app()) as client:
            payload = {"sql": "SELECT 1 AS x", "idempotency_key": key}
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(
                    pool.map(lambda _: client.post("/api/v1/query-runs", json=payload), range(4))
                )
            assert all(r.status_code in (200, 202) for r in responses)
            assert sum(1 for r in responses if r.status_code == 202) == 1
            ids = {r.json()["run"]["id"] for r in responses}
            assert len(ids) == 1

            assert _count_runs_with_key(sessions, key) == 1
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_history_pages_stably_by_creation_order():
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            created = []
            for i in range(25):
                r = client.post("/api/v1/query-runs", json={"sql": f"SELECT {i} AS n"})
                assert r.status_code == 202
                created.append(r.json()["run"]["id"])

            seen: list[dict] = []
            cursor = None
            while True:
                url = "/api/v1/query-runs?limit=10"
                if cursor is not None:
                    url += f"&cursor={cursor}"
                page = client.get(url)
                assert page.status_code == 200
                body = page.json()
                assert {"id", "state", "created_at", "attempt"} <= set(body["runs"][0])
                seen.extend(
                    {"id": run["id"], "created_at": run["created_at"]}
                    for run in body["runs"]
                )
                cursor = body["next_cursor"]
                if cursor is None:
                    break

            # 合同是 (created_at, id) 双键降序的稳定分页：无重复、无遗漏、
            # 键序非升。id 全局降序只是 created_at 单调时的推论——并发
            # 提交会让事务时间戳与 id 分配交错（created_at 倒挂），持久
            # 测试库里真实存在这种行，不能作为断言。
            ids = [run["id"] for run in seen]
            assert len(ids) == len(set(ids)), "分页不得重复返回同一运行"
            keys = [(run["created_at"], run["id"]) for run in seen]
            assert keys == sorted(keys, reverse=True), "分页必须按双键降序稳定推进"
            ours = [run_id for run_id in ids if run_id in set(created)]
            assert ours == sorted(created, reverse=True)

            # 默认 limit 不超过 20
            default_page = client.get("/api/v1/query-runs")
            assert default_page.status_code == 200
            assert len(default_page.json()["runs"]) <= 20
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
