"""v0.2.0 异步链路集成测试（#8）：受理 → 认领 → 执行 → 原子发布 → 快照。

在 api-test 容器内运行；worker 认领与发布走真实网关缝（queue.py），
SQL 执行走真实只读连接，证明「终态+快照不可分割发布」与身份边界。
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.db import create_platform_engine, create_platform_session_factory
from app.execute.executor import execute_readonly
from app.main import create_app
from app.runs import queue

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]
READONLY_DSN = os.environ["ANALYTICS_READONLY_DSN"]


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def test_full_async_pipeline_publishes_snapshot_atomically():
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            resp = client.post(
                "/api/v1/query-runs",
                json={"sql": "SELECT region, count(*) AS n FROM customers GROUP BY region ORDER BY region"},
            )
            assert resp.status_code == 202
            run = resp.json()["run"]
            assert run["state"] == "queued"
            run_id = run["id"]

            # 成功前：result 未就绪
            assert client.get(f"/api/v1/query-runs/{run_id}/result").status_code == 409

            # worker 认领（真实网关缝）
            claim = queue.claim_next(sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id)
            assert claim is not None and claim.run_id == run_id
            assert claim.generation == 1

            # 认领后运行进入 running；result 仍未就绪
            state = client.get(f"/api/v1/query-runs/{run_id}").json()["run"]["state"]
            assert state == "running"
            assert client.get(f"/api/v1/query-runs/{run_id}/result").status_code == 409

            # 真实只读连接执行（worker 的执行路径）
            import psycopg

            with psycopg.connect(READONLY_DSN) as conn:
                result = execute_readonly(claim.sql, conn=conn, max_rows=500)
            snap_fields = queue.build_snapshot(result)
            ok = queue.publish_success(
                sessions,
                claim,
                columns=snap_fields["columns"],
                rows=snap_fields["rows"],
                row_count=result.row_count,
                truncated=result.truncated,
                duration_ms=result.duration_ms,
                size_bytes=snap_fields["size_bytes"],
                retention_hours=24,
            )
            assert ok

            # 成功后：终态 + 快照一次到位
            body = client.get(f"/api/v1/query-runs/{run_id}").json()["run"]
            assert body["state"] == "succeeded"
            snap = client.get(f"/api/v1/query-runs/{run_id}/result")
            assert snap.status_code == 200
            data = snap.json()["result"]
            assert data["row_count"] == 5
            assert data["columns"][0]["name"] == "region"
            assert data["expires_at"] is not None
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_stale_generation_cannot_publish():
    """代过期的旧执行者发布被拒绝：fencing 生效（ADR-0015）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    try:
        with TestClient(create_app()) as client:
            run_id = client.post(
                "/api/v1/query-runs", json={"sql": "SELECT 1 AS x"}
            ).json()["run"]["id"]

            stale = queue.claim_next(sessions, worker_id="old-worker", lease_seconds=60, run_id=run_id)
            # 模拟旧执行者失联后的接管路径：租约视为已失效，行重新可认领
            with sessions() as s:
                s.execute(
                    text("UPDATE query_runs SET state = 'queued' WHERE id = :rid"),
                    {"rid": stale.run_id},
                )
                s.commit()
            fresh = queue.claim_next(sessions, worker_id="new-worker", lease_seconds=60, run_id=run_id)
            assert fresh is not None and fresh.run_id == stale.run_id
            assert fresh.generation == stale.generation + 1

            ok = queue.publish_success(
                sessions,
                stale,
                columns=[{"name": "x", "type": "int4"}],
                rows=[[1]],
                row_count=1,
                truncated=False,
                duration_ms=1,
                size_bytes=10,
                retention_hours=24,
            )
            assert not ok, "旧代发布必须被拒绝"
    finally:
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_worker_path_uses_readonly_identity_only():
    """worker 执行路径的真实连接无法写入 analytics（身份边界第二道）。"""
    import psycopg

    with psycopg.connect(READONLY_DSN) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM customers")
            conn.commit()
            raise AssertionError("只读身份不应能提交写事务")
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction):
            conn.rollback()
