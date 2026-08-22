"""结果保留期与幂等清理测试（#14）：24 小时以终态发布时间为锚点。

时间控制不注入时钟，而是把已固化的时间戳整体回拨（finished_at 与
expires_at 同步平移），模拟「发布后经过了 N 秒」——判定逻辑面对的
始终是数据库里的真实 now() 与真实存储时刻。
"""

from __future__ import annotations

import os
from datetime import timedelta

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_platform_engine, create_platform_session_factory
from app.execute.executor import execute_readonly
from app.main import create_app
from app.runs import queue

READONLY_DSN = os.environ["ANALYTICS_READONLY_DSN"]
PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]

RETENTION = timedelta(hours=24)


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def _submit(client) -> int:
    resp = client.post("/api/v1/query-runs", json={"sql": "SELECT 1 AS x"})
    assert resp.status_code == 202
    return resp.json()["run"]["id"]


def _publish_succeeded(sessions, run_id: int) -> None:
    """走 worker 同款路径：认领 → 只读执行 → 终态+快照原子发布。"""
    claim = queue.claim_next(sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id)
    assert claim is not None
    with psycopg.connect(READONLY_DSN) as conn:
        result = execute_readonly("SELECT 1 AS x", conn=conn, max_rows=500)
    snap = queue.build_snapshot(result)
    assert queue.publish_success(
        sessions,
        claim,
        columns=snap["columns"],
        rows=snap["rows"],
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
        size_bytes=snap["size_bytes"],
        retention_hours=24,
    )


def _publish_failed(sessions, run_id: int) -> None:
    claim = queue.claim_next(sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id)
    assert claim is not None
    assert queue.publish_failure(
        sessions, claim, error_code="QY_TEST_FAILURE", error_message="测试失败终态"
    )


def _age_run(sessions, run_id: int, elapsed: timedelta) -> None:
    """把终态发布时间整体回拨 elapsed（finished_at 与 expires_at 同步平移）。"""
    with sessions() as s:
        s.execute(
            text(
                "UPDATE query_runs SET finished_at = finished_at "
                "- make_interval(secs => :sec) WHERE id = :rid"
            ),
            {"sec": elapsed.total_seconds(), "rid": run_id},
        )
        s.execute(
            text(
                "UPDATE query_run_snapshots SET expires_at = expires_at "
                "- make_interval(secs => :sec) WHERE run_id = :rid"
            ),
            {"sec": elapsed.total_seconds(), "rid": run_id},
        )
        s.commit()


def _snapshot_row(sessions, run_id: int):
    with sessions() as s:
        return s.execute(
            text(
                "SELECT expires_at FROM query_run_snapshots WHERE run_id = :rid"
            ),
            {"rid": run_id},
        ).fetchone()


def test_expires_at_anchored_to_publish_time():
    """expires_at 与 finished_at 的差恰为 24 小时：截止时刻自终态发布
    时间起算（不是提交时间 created_at，也不是读取时间）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client)
        _publish_succeeded(sessions, run_id)

        with sessions() as s:
            finished_at, expires_at = s.execute(
                text(
                    "SELECT finished_at, (SELECT expires_at FROM query_run_snapshots "
                    "WHERE run_id = query_runs.id) FROM query_runs WHERE id = :rid"
                ),
                {"rid": run_id},
            ).one()
        assert finished_at is not None
        assert expires_at - finished_at == RETENTION

        with TestClient(create_app()) as client:
            body = client.get(f"/api/v1/query-runs/{run_id}/result").json()["result"]
        assert body["expires_at"] == expires_at.isoformat()
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_boundary_flips_exactly_at_retention():
    """保留期内可读；越过 24 小时边界即 410（锚点=终态发布时间）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            inside_id = _submit(client)
            outside_id = _submit(client)
        _publish_succeeded(sessions, inside_id)
        _publish_succeeded(sessions, outside_id)

        # 发布后 24 小时差 30 秒：仍在保留期内
        _age_run(sessions, inside_id, RETENTION - timedelta(seconds=30))
        # 发布后 24 小时多 1 秒：已过保留期
        _age_run(sessions, outside_id, RETENTION + timedelta(seconds=1))

        with TestClient(create_app()) as client:
            assert client.get(f"/api/v1/query-runs/{inside_id}/result").status_code == 200
            expired = client.get(f"/api/v1/query-runs/{outside_id}/result")
        assert expired.status_code == 410
        assert expired.json()["detail"]["code"] == "QY_RESULT_EXPIRED"
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_expired_result_410_but_audit_still_readable():
    """过期后快照不可读（410），审计记录仍可经 GET 运行读到（验收 1）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client)
        _publish_succeeded(sessions, run_id)
        _age_run(sessions, run_id, RETENTION + timedelta(seconds=1))

        with TestClient(create_app()) as client:
            result = client.get(f"/api/v1/query-runs/{run_id}/result")
            audit = client.get(f"/api/v1/query-runs/{run_id}")
        assert result.status_code == 410
        assert audit.status_code == 200
        assert audit.json()["run"]["state"] == "succeeded"
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_purge_is_idempotent_and_selective():
    """清理只删超期快照、不动保留期内快照与任何审计行；重复执行为空操作
    （验收 2）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    # 前序测试可能留下已老化的过期快照：先清干净，让删除行数断言精确。
    queue.purge_expired_snapshots(sessions)
    try:
        with TestClient(create_app()) as client:
            fresh_id = _submit(client)
            expired_id = _submit(client)
        _publish_succeeded(sessions, fresh_id)
        _publish_succeeded(sessions, expired_id)
        _age_run(sessions, expired_id, RETENTION + timedelta(seconds=1))

        assert queue.purge_expired_snapshots(sessions) == 1
        assert _snapshot_row(sessions, expired_id) is None
        assert _snapshot_row(sessions, fresh_id) is not None

        # 重复执行：没有新的超期快照，保留期内数据原样
        assert queue.purge_expired_snapshots(sessions) == 0
        assert _snapshot_row(sessions, fresh_id) is not None

        with sessions() as s:
            states = s.execute(
                text("SELECT id, state FROM query_runs WHERE id IN (:a, :b)"),
                {"a": fresh_id, "b": expired_id},
            ).all()
        assert {row_id: state for row_id, state in states} == {
            fresh_id: "succeeded",
            expired_id: "succeeded",
        }, "审计行永久保留，清理不得触碰"

        with TestClient(create_app()) as client:
            assert client.get(f"/api/v1/query-runs/{fresh_id}/result").status_code == 200
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_purged_expired_run_still_410_anchored_on_publish_time():
    """快照被清理后 410 语义不丢：过期判定回退到 finished_at + 保留期，
    仍以终态发布时间为锚点（验收 3 的清理后形态）。保留期内快照缺失
    则是另一回事——不变量破坏，409 而非 410。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    # 同上：先清掉前序测试残留的超期快照，使「删 1 行」断言精确。
    queue.purge_expired_snapshots(sessions)
    try:
        with TestClient(create_app()) as client:
            expired_id = _submit(client)
            broken_id = _submit(client)
        _publish_succeeded(sessions, expired_id)
        _publish_succeeded(sessions, broken_id)

        _age_run(sessions, expired_id, RETENTION + timedelta(seconds=60))
        assert queue.purge_expired_snapshots(sessions) == 1
        # 保留期内人为删掉快照：模拟 succeeded 无快照的不变量破坏
        with sessions() as s:
            s.execute(
                text("DELETE FROM query_run_snapshots WHERE run_id = :rid"),
                {"rid": broken_id},
            )
            s.commit()

        with TestClient(create_app()) as client:
            purged_read = client.get(f"/api/v1/query-runs/{expired_id}/result")
            broken_read = client.get(f"/api/v1/query-runs/{broken_id}/result")
        assert purged_read.status_code == 410
        assert purged_read.json()["detail"]["code"] == "QY_RESULT_EXPIRED"
        assert broken_read.status_code == 409
        assert broken_read.json()["detail"]["code"] == "QY_RESULT_NOT_AVAILABLE"
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_old_failed_run_is_409_not_410():
    """从未有过结果的终态（failed）不因运行变旧而改口径：409 恒定。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client)
        _publish_failed(sessions, run_id)
        _age_run(sessions, run_id, RETENTION * 2)

        with TestClient(create_app()) as client:
            resp = client.get(f"/api/v1/query-runs/{run_id}/result")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "QY_RESULT_NOT_AVAILABLE"
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
