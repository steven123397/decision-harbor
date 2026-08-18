"""取消与重试操作集成测试（#12）：完整状态机语义与 HTTP 合同。

在 api-test 容器内运行，走真实队列网关缝（queue.py）与进程内 ASGI：
排队取消确定生效、运行中取消 best effort 中止底层查询、取消与终态
发布的竞态只有一个结局、cancel 幂等与 409、retry 的 retry_of 关系与
409 资格判定（ADR-0018 状态码合同）。
"""

from __future__ import annotations

import os
import threading
import time

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_platform_engine, create_platform_session_factory
from app.execute.executor import ExecutionFailure, execute_readonly
from app.main import create_app
from app.runs import queue

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]
READONLY_DSN = os.environ["ANALYTICS_READONLY_DSN"]

CLEANUP_ERROR = ("QY_TEST_CLEANUP", "集成测试收尾")


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def _submit(client: TestClient, sql: str) -> int:
    resp = client.post("/api/v1/query-runs", json={"sql": sql})
    assert resp.status_code == 202
    return resp.json()["run"]["id"]


def _submit_rejected(client: TestClient, sql: str) -> int:
    """提交必被策略拒绝的 SQL：422 同步落定 rejected 终态。"""
    resp = client.post("/api/v1/query-runs", json={"sql": sql})
    assert resp.status_code == 422
    return resp.json()["run"]["id"]


def _row(sessions, run_id: int):
    with sessions() as session:
        return session.execute(
            text(
                "SELECT state, attempt, generation, worker_id, retry_of, "
                "error_code FROM query_runs WHERE id = :rid"
            ),
            {"rid": run_id},
        ).fetchone()


def _finalize(sessions, claims) -> None:
    for claim in claims:
        if claim is not None:
            queue.publish_failure(
                sessions, claim, error_code=CLEANUP_ERROR[0], error_message=CLEANUP_ERROR[1]
            )


def test_cancel_queued_run_takes_definite_effect():
    """排队运行取消：确定生效，终态 cancelled，永不被认领执行。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 21 AS x")
            resp = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert resp.status_code == 200
            assert resp.json()["run"]["state"] == "cancelled"

            # 幂等：重复 cancel 同样 200，状态不变
            again = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert again.status_code == 200
            assert again.json()["run"]["state"] == "cancelled"

        row = _row(sessions, run_id)
        assert row.state == "cancelled"
        # 取消后的排队运行不再是可执行候选
        assert queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        ) is None
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_cancel_running_run_transitions_to_cancelling():
    """运行中取消：转入 cancelling（202），执行者随后落 cancelled 终态。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 22 AS x")
        claim = queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        )
        claims.append(claim)

        with TestClient(create_app()) as client:
            resp = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert resp.status_code == 202
            assert resp.json()["run"]["state"] == "cancelling"

        # 执行者收尾：cancelling 上的终态发布被 fencing 拒绝，
        # 转由 finalize_cancelled 落 cancelled 终态
        assert not queue.publish_success(
            sessions, claim,
            columns=[{"name": "x", "type": "int4"}], rows=[[22]],
            row_count=1, truncated=False, duration_ms=1,
            size_bytes=10, retention_hours=24,
        ), "取消获胜后不得再发布查询结果"
        assert queue.finalize_cancelled(sessions, claim)
        row = _row(sessions, run_id)
        assert row.state == "cancelled"
        # cancelled 运行没有可读结果
        with TestClient(create_app()) as client:
            result = client.get(f"/api/v1/query-runs/{run_id}/result")
            assert result.status_code == 409
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_cancel_racing_with_publish_has_single_outcome():
    """取消与成功的竞态：发布先落定则 cancel 409；只有一方能赢。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 23 AS x")
        claim = queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        )
        claims.append(claim)
        # 成功终态先发布：查询结果成立，取消随后 409
        assert queue.publish_success(
            sessions, claim,
            columns=[{"name": "x", "type": "int4"}], rows=[[23]],
            row_count=1, truncated=False, duration_ms=1,
            size_bytes=10, retention_hours=24,
        )
        with TestClient(create_app()) as client:
            resp = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "run_not_cancellable"
            # succeeded 结果可读（取消没有生效）
            assert client.get(f"/api/v1/query-runs/{run_id}/result").status_code == 200
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_cancel_of_terminal_runs_conflicts():
    """succeeded / failed / rejected 的 cancel 一律 409 run_not_cancellable。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            ok_id = _submit(client, "SELECT 24 AS x")
            fail_id = _submit(client, "SELECT 25 AS x")
            rejected_id = _submit_rejected(client, "DELETE FROM customers")
            assert client.get(f"/api/v1/query-runs/{rejected_id}").json()["run"][
                "state"
            ] == "rejected"
        ok_claim = queue.claim_next(sessions, worker_id="it-w", lease_seconds=60, run_id=ok_id)
        fail_claim = queue.claim_next(sessions, worker_id="it-w", lease_seconds=60, run_id=fail_id)
        claims += [ok_claim, fail_claim]
        assert queue.publish_success(
            sessions, ok_claim,
            columns=[{"name": "x", "type": "int4"}], rows=[[24]],
            row_count=1, truncated=False, duration_ms=1,
            size_bytes=10, retention_hours=24,
        )
        assert queue.publish_failure(
            sessions, fail_claim, error_code="QY_EXECUTION_ERROR", error_message="确定性失败"
        )
        with TestClient(create_app()) as client:
            for run_id in (ok_id, fail_id, rejected_id):
                resp = client.post(f"/api/v1/query-runs/{run_id}/cancel")
                assert resp.status_code == 409, f"运行 {run_id} 的 cancel 应 409"
                assert resp.json()["detail"]["code"] == "run_not_cancellable"
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_running_query_aborts_on_cancel_seam():
    """执行中的真实查询经 conn.cancel() 中止：取消获胜路径端到端。

    模拟 worker 的完整路径：认领 → 执行慢查询 → cancel 请求落 cancelling
    → 连接层取消 → 执行失败处置被 fencing 拒绝 → finalize_cancelled。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(
                client,
                "SELECT count(*) FROM order_items a, order_items b, orders o",
            )
        claim = queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        )
        claims.append(claim)

        outcome: dict = {}

        def execute():
            with psycopg.connect(READONLY_DSN) as conn:
                outcome["conn"] = conn
                try:
                    execute_readonly(claim.sql, conn=conn, max_rows=100)
                    outcome["result"] = "completed"
                except ExecutionFailure as exc:
                    outcome["result"] = exc.code

        thread = threading.Thread(target=execute)
        thread.start()
        time.sleep(0.8)  # 等查询真正在执行
        with TestClient(create_app()) as client:
            resp = client.post(f"/api/v1/query-runs/{run_id}/cancel")
            assert resp.status_code == 202
        outcome["conn"].cancel()  # worker keeper 检测 cancelling 后的动作
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert outcome["result"] == "QY_TIMEOUT", "底层中止以 QueryCanceled 形态到达"

        assert queue.requeue_or_fail(
            sessions, claim,
            error_code="QY_TIMEOUT", error_message="查询执行超时，已被语句超时限制中止",
        ) == "fenced", "cancelling 运行不得回队"
        assert queue.finalize_cancelled(sessions, claim)
        row = _row(sessions, run_id)
        assert row.state == "cancelled"
        with sessions() as session:
            snap = session.execute(
                text("SELECT 1 FROM query_run_snapshots WHERE run_id = :rid"),
                {"rid": run_id},
            ).fetchone()
        assert snap is None, "取消获胜不得留下结果快照"
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_expired_cancelling_run_swept_to_cancelled():
    """cancelling 遗孤清扫：执行者收尾前崩溃（租约过期超宽限）由清扫
    补上 cancelled 终态；宽限期内不扫。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 31 AS x")
        claim = queue.claim_next(sessions, worker_id="it-w", lease_seconds=60, run_id=run_id)
        claims.append(claim)
        with TestClient(create_app()) as client:
            assert client.post(f"/api/v1/query-runs/{run_id}/cancel").status_code == 202
        # 模拟执行者崩溃：租约已过期（未续期）
        with sessions() as session:
            session.execute(
                text(
                    "UPDATE query_runs SET lease_expires_at = now() - interval '1 second' "
                    "WHERE id = :rid"
                ),
                {"rid": run_id},
            )
            session.commit()
        # 宽限期内不清扫
        assert queue.finalize_expired_cancelling(sessions, grace_seconds=30) == 0
        with sessions() as session:
            session.execute(
                text(
                    "UPDATE query_runs SET lease_expires_at = "
                    "now() - interval '61 seconds' WHERE id = :rid"
                ),
                {"rid": run_id},
            )
            session.commit()
        assert queue.finalize_expired_cancelling(sessions, grace_seconds=30) >= 1
        assert _row(sessions, run_id).state == "cancelled"
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_retry_failed_run_creates_new_run_with_retry_of():
    """failed 运行的 retry：202 + 新运行（retry_of 指向原运行），可独立终态。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 26 AS x")
        claim = queue.claim_next(sessions, worker_id="it-w", lease_seconds=60, run_id=run_id)
        claims.append(claim)
        assert queue.publish_failure(
            sessions, claim, error_code="QY_TIMEOUT", error_message="耗尽后失败"
        )

        with TestClient(create_app()) as client:
            resp = client.post(f"/api/v1/query-runs/{run_id}/retry")
            assert resp.status_code == 202
            new_run = resp.json()["run"]
            assert new_run["id"] != run_id
            assert new_run["retry_of"] == run_id
            assert new_run["state"] == "queued"
            assert new_run["attempt"] == 1

            # 新运行独立走到成功终态
            new_claim = queue.claim_next(
                sessions, worker_id="it-w", lease_seconds=60, run_id=new_run["id"]
            )
            claims.append(new_claim)
            assert new_claim.attempt == 1, "新运行的 attempt 预算独立"
            assert queue.publish_success(
                sessions, new_claim,
                columns=[{"name": "x", "type": "int4"}], rows=[[26]],
                row_count=1, truncated=False, duration_ms=1,
                size_bytes=10, retention_hours=24,
            )
            assert client.get(
                f"/api/v1/query-runs/{new_run['id']}/result"
            ).status_code == 200
            # 原运行保持 failed 终态，不复活
            assert client.get(f"/api/v1/query-runs/{run_id}").json()["run"][
                "state"
            ] == "failed"
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_retry_cancelled_run_and_conflict_states():
    """cancelled 可 retry；succeeded / rejected / 排队与运行中一律 409。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims = []
    try:
        with TestClient(create_app()) as client:
            cancelled_id = _submit(client, "SELECT 27 AS x")
            succeeded_id = _submit(client, "SELECT 28 AS x")
            rejected_id = _submit_rejected(client, "DELETE FROM customers")
            queued_id = _submit(client, "SELECT 29 AS x")

            client.post(f"/api/v1/query-runs/{cancelled_id}/cancel")
            ok_claim = queue.claim_next(
                sessions, worker_id="it-w", lease_seconds=60, run_id=succeeded_id
            )
            claims.append(ok_claim)
            running_id = _submit(client, "SELECT 30 AS x")
            running_claim = queue.claim_next(
                sessions, worker_id="it-w", lease_seconds=60, run_id=running_id
            )
            claims.append(running_claim)
            assert queue.publish_success(
                sessions, ok_claim,
                columns=[{"name": "x", "type": "int4"}], rows=[[28]],
                row_count=1, truncated=False, duration_ms=1,
                size_bytes=10, retention_hours=24,
            )

            resp = client.post(f"/api/v1/query-runs/{cancelled_id}/retry")
            assert resp.status_code == 202
            assert resp.json()["run"]["retry_of"] == cancelled_id

            for run_id, why in (
                (succeeded_id, "succeeded"),
                (rejected_id, "rejected"),
                (queued_id, "queued"),
                (running_id, "running"),
            ):
                resp = client.post(f"/api/v1/query-runs/{run_id}/retry")
                assert resp.status_code == 409, f"{why} 的 retry 应 409"
                assert resp.json()["detail"]["code"] == "run_not_retryable"

            # rejected 的指引：修改 SQL 后重新提交
            detail = client.post(
                f"/api/v1/query-runs/{rejected_id}/retry"
            ).json()["detail"]
            assert "重新提交" in detail["message"]

            # 取消/重试不存在或非法 id：404
            assert client.post("/api/v1/query-runs/999999/cancel").status_code == 404
            assert client.post("/api/v1/query-runs/abc/retry").status_code == 404
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
