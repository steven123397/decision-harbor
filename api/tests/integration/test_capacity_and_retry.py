"""全局并发闸门、attempt 硬上界与自动重试分类集成测试（#11）。

在 api-test 容器内运行，走真实队列网关缝（queue.py）：容量以数据库中
有效租约数为唯一事实源（跨执行者生效）、任何故障序列单次运行总执行
次数不超过 3、基础设施类失败自动回队而确定性 SQL 错误直接终态。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_platform_engine, create_platform_session_factory
from app.main import create_app
from app.runs import queue

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]

CLEANUP_ERROR = ("QY_TEST_CLEANUP", "集成测试收尾")


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def _clean_slates(sessions) -> None:
    """开测前把历史遗留的非终态行清场：容量闸门从 0 起算，认领顺序确定。"""
    with sessions() as session:
        session.execute(
            text(
                "UPDATE query_runs SET state = 'failed', "
                "error_code = :code, error_message = :msg, finished_at = now() "
                "WHERE state IN ('received', 'queued', 'running', 'cancelling')"
            ),
            {"code": CLEANUP_ERROR[0], "msg": CLEANUP_ERROR[1]},
        )
        session.commit()


def _submit(client: TestClient, sql: str) -> int:
    resp = client.post("/api/v1/query-runs", json={"sql": sql})
    assert resp.status_code == 202
    return resp.json()["run"]["id"]


def _submit_many(client: TestClient, n: int) -> list[int]:
    return [_submit(client, f"SELECT {i} AS x") for i in range(n)]


def _row(sessions, run_id: int):
    with sessions() as session:
        return session.execute(
            text(
                "SELECT state, attempt, generation, worker_id, lease_expires_at, "
                "error_code FROM query_runs WHERE id = :rid"
            ),
            {"rid": run_id},
        ).fetchone()


def _expire_lease(sessions, run_id: int) -> None:
    with sessions() as session:
        session.execute(
            text(
                "UPDATE query_runs SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :rid"
            ),
            {"rid": run_id},
        )
        session.commit()


def _finalize(sessions, claims) -> None:
    for claim in claims:
        if claim is not None:
            queue.publish_failure(
                sessions, claim, error_code=CLEANUP_ERROR[0], error_message=CLEANUP_ERROR[1]
            )


def test_global_capacity_gate_admits_four_and_queues_fifth():
    """全局并发 4：第 5 个运行不被认领（排队等待），释放容量后可认领。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_ids = _submit_many(client, 5)

        # 两个执行者交替认领：容量闸门数的是全库有效租约，与谁认领无关。
        for i in range(4):
            worker = "it-worker-a" if i % 2 == 0 else "it-worker-b"
            claim = queue.claim_next(
                sessions, worker_id=worker, lease_seconds=60, capacity=4
            )
            assert claim is not None, f"第 {i + 1} 个认领不应被容量闸门阻挡"
            claims.append(claim)

        fifth = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, capacity=4
        )
        assert fifth is None, "全局并发已满 4，第 5 个运行必须排队等待"

        # 一个运行终态释放容量后，第 5 个才能被认领。
        done_id = claims[0].run_id
        assert queue.publish_failure(
            sessions,
            claims[0],
            error_code=CLEANUP_ERROR[0],
            error_message=CLEANUP_ERROR[1],
        )
        claims = claims[1:]
        fifth = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, capacity=4
        )
        assert fifth is not None
        claims.append(fifth)
        assert set(run_ids) == {done_id} | {c.run_id for c in claims}
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_concurrent_claims_never_exceed_global_capacity():
    """8 个执行者并发认领 8 个运行：同时成功的恰好 4 个，互不重复。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            _submit_many(client, 8)

        def claim_once(i: int):
            return queue.claim_next(
                sessions, worker_id=f"it-worker-{i}", lease_seconds=60, capacity=4
            )

        with ThreadPoolExecutor(max_workers=8) as pool_exec:
            results = list(pool_exec.map(claim_once, range(8)))
        claims = [c for c in results if c is not None]

        assert len(claims) == 4, f"全局并发 4，并发认领成功数应为 4，实际 {len(claims)}"
        run_ids = [c.run_id for c in claims]
        assert len(run_ids) == len(set(run_ids)), "同一运行被重复认领"
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_takeover_chain_stops_at_three_attempts():
    """接管链：attempt 1→2→3，耗尽后不再可认领，过期清扫转入 failed。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 11 AS x")

        claim = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        assert claim is not None and claim.attempt == 1
        for expect_attempt in (2, 3):
            _expire_lease(sessions, run_id)
            takeover = queue.claim_next(
                sessions, worker_id=f"it-worker-t{expect_attempt}",
                lease_seconds=60, run_id=run_id,
            )
            assert takeover is not None, f"attempt {expect_attempt} 应可接管"
            assert takeover.attempt == expect_attempt, "接管重跑必须递增 attempt"
            claims.append(takeover)

        _expire_lease(sessions, run_id)
        refused = queue.claim_next(
            sessions, worker_id="it-worker-t4", lease_seconds=60, run_id=run_id
        )
        assert refused is None, "attempt 达到 3 后不得出现第 4 次执行"

        # 宽限期内不清扫：续期抖动的执行者仍有复活窗口（ADR-0019）。
        assert queue.fail_expired_exhausted(sessions, grace_seconds=30) == 0
        with sessions() as session:
            session.execute(
                text(
                    "UPDATE query_runs SET lease_expires_at = "
                    "now() - interval '61 seconds' WHERE id = :rid"
                ),
                {"rid": run_id},
            )
            session.commit()
        swept = queue.fail_expired_exhausted(sessions, grace_seconds=30)
        assert swept >= 1, "过期超过宽限且耗尽的运行应被清扫"
        row = _row(sessions, run_id)
        assert row.state == "failed"
        assert row.attempt == 3
        assert row.error_code == queue.ATTEMPTS_EXHAUSTED_CODE
        claims = []  # 清扫已终态，无需再收尾
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_infra_failure_requeues_until_attempts_exhausted():
    """基础设施类失败自动回队重跑（attempt 递增），第 3 次失败落终态。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 12 AS x")

        for expect_attempt in (1, 2):
            claim = queue.claim_next(
                sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
            )
            assert claim is not None and claim.attempt == expect_attempt
            claims.append(claim)
            outcome = queue.requeue_or_fail(
                sessions, claim,
                error_code="QY_TIMEOUT", error_message="查询执行超时，已被语句超时限制中止",
            )
            assert outcome == "requeued", f"attempt {expect_attempt} 的 infra 失败应回队"
            row = _row(sessions, run_id)
            assert row.state == "queued", "回队后运行等待下一次认领"
            assert row.worker_id is None and row.lease_expires_at is None
            assert row.attempt == expect_attempt, "attempt 在认领时递增，回队不预递增"

        claim = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        assert claim is not None and claim.attempt == 3
        outcome = queue.requeue_or_fail(
            sessions, claim,
            error_code="QY_TIMEOUT", error_message="查询执行超时，已被语句超时限制中止",
        )
        assert outcome == "failed", "第 3 次执行失败后不得再回队"
        row = _row(sessions, run_id)
        assert row.state == "failed" and row.attempt == 3
        assert row.error_code == "QY_TIMEOUT", "终态保留最后一次真实错误码"
        claims = []  # 已终态
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_deterministic_failure_fails_immediately():
    """确定性 SQL 错误不重试：attempt 1 直接 failed。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 13 AS x")
        claim = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        claims.append(claim)
        outcome = queue.requeue_or_fail(
            sessions, claim,
            error_code="QY_EXECUTION_ERROR", error_message="查询执行失败，请检查列名与表达式",
        )
        assert outcome == "failed"
        row = _row(sessions, run_id)
        assert row.state == "failed" and row.attempt == 1
        claims = []
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_stale_claim_cannot_requeue_after_takeover():
    """代过期：失去所有权的执行者回队请求同样被 fencing 拒绝。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 14 AS x")
        stale = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        _expire_lease(sessions, run_id)
        fresh = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, run_id=run_id
        )
        claims.append(fresh)

        outcome = queue.requeue_or_fail(
            sessions, stale, error_code="QY_TIMEOUT", error_message="旧执行者超时"
        )
        assert outcome == "fenced", "旧代回队必须被拒绝"
        row = _row(sessions, run_id)
        assert row.state == "running" and row.worker_id == "it-worker-b"
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_claim_excludes_own_active_runs():
    """自接管排除：执行者不认领自己仍在执行（租约已过期）的运行。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    _clean_slates(sessions)
    claims = []
    try:
        with TestClient(create_app()) as client:
            first, second = _submit_many(client, 2)
        mine = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=first
        )
        claims.append(mine)
        _expire_lease(sessions, first)  # 自己卡死、租约过期

        other = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60,
            exclude_run_ids={first},
        )
        assert other is not None and other.run_id == second, \
            "排除自己卡死的运行后应认领下一个排队运行"
        claims.append(other)
        assert queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60,
            exclude_run_ids={first},
        ) is None, "排除集合外的队列已空"

        takeover = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, run_id=first
        )
        assert takeover is not None and takeover.attempt == 2, \
            "其他执行者仍可接管该运行"
        claims.append(takeover)
    finally:
        _finalize(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
