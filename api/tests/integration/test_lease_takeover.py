"""队列租约、接管与 fencing 集成测试（#10）：多执行者协同的所有权语义。

在 api-test 容器内运行，走真实队列网关缝（queue.py）：并发认领不重复、
租约未过期不可夺走、过期后接管递增 attempt/generation、旧执行者发布
被 fencing 拒绝且不覆盖新执行者、续期要求当前所有权。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import create_platform_engine, create_platform_session_factory
from app.main import create_app
from app.runs import queue
from app.runs.queue import Claim

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]

CLEANUP_ERROR = ("QY_TEST_CLEANUP", "集成测试收尾")


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def _submit(client: TestClient, sql: str) -> int:
    resp = client.post("/api/v1/query-runs", json={"sql": sql})
    assert resp.status_code == 202
    return resp.json()["run"]["id"]


def _finalize_all(sessions, claims: list[Claim]) -> None:
    """把测试认领的行推入终态，避免遗留 running 行被真实 worker 接管。"""
    for claim in claims:
        queue.publish_failure(
            sessions, claim, error_code=CLEANUP_ERROR[0], error_message=CLEANUP_ERROR[1]
        )


def test_concurrent_claims_never_duplicate():
    """两个执行者并发抽干队列：同一运行只被认领一次（SKIP LOCKED）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims: list[Claim] = []
    try:
        with TestClient(create_app()) as client:
            run_ids = {
                _submit(client, f"SELECT {n} AS x") for n in range(1, 7)
            }
            assert len(run_ids) == 6

        def drain(worker: str) -> list[Claim]:
            got = []
            while True:
                claim = queue.claim_next(
                    sessions, worker_id=worker, lease_seconds=60
                )
                if claim is None:
                    return got
                got.append(claim)

        with ThreadPoolExecutor(max_workers=2) as pool_exec:
            for part in pool_exec.map(drain, ("it-worker-a", "it-worker-b")):
                claims.extend(part)

        claimed_ids = [c.run_id for c in claims]
        # 本测试提交的 6 条各被认领一次；库中可能有历史遗留行被顺带认领，
        # 但任何一行都不允许被认领两次。
        assert len(claimed_ids) == len(set(claimed_ids)), "同一运行被重复认领"
        assert run_ids.issubset(set(claimed_ids))
        for claim in claims:
            if claim.run_id in run_ids:
                assert claim.generation == 1
    finally:
        _finalize_all(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_unexpired_lease_is_not_claimable():
    """租约未过期：其他执行者不可认领该运行。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims: list[Claim] = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 1 AS x")
        first = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        claims.append(first)
        second = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, run_id=run_id
        )
        assert first is not None and first.attempt == 1
        assert second is None, "租约未过期的运行不应被他人认领"
    finally:
        _finalize_all(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def _expire_lease(sessions, run_id: int) -> None:
    with sessions() as session:
        session.execute(
            text("UPDATE query_runs SET lease_expires_at = now() - interval '1 second' "
                 "WHERE id = :rid"),
            {"rid": run_id},
        )
        session.commit()


def test_takeover_after_lease_expiry():
    """租约过期：其他执行者接管，attempt 与 generation 递增。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims: list[Claim] = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 2 AS x")
        first = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        claims.append(first)
        _expire_lease(sessions, run_id)

        takeover = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, run_id=run_id
        )
        assert takeover is not None
        claims.append(takeover)
        assert takeover.attempt == first.attempt + 1, "接管必须递增 attempt"
        assert takeover.generation == first.generation + 1

        with sessions() as session:
            row = session.execute(
                text("SELECT state, worker_id FROM query_runs WHERE id = :rid"),
                {"rid": run_id},
            ).fetchone()
        assert row.state == "running" and row.worker_id == "it-worker-b"
    finally:
        _finalize_all(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_stale_executor_cannot_finalize_or_overwrite():
    """代过期的旧执行者：终态发布被拒，且不覆盖新执行者的所有权。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims: list[Claim] = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 3 AS x")
        stale = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=60, run_id=run_id
        )
        _expire_lease(sessions, run_id)
        fresh = queue.claim_next(
            sessions, worker_id="it-worker-b", lease_seconds=60, run_id=run_id
        )
        claims.append(fresh)

        ok = queue.publish_success(
            sessions,
            stale,
            columns=[{"name": "x", "type": "int4"}],
            rows=[[3]],
            row_count=1,
            truncated=False,
            duration_ms=1,
            size_bytes=10,
            retention_hours=24,
        )
        assert not ok, "旧代成功发布必须被拒绝"
        ok = queue.publish_failure(
            sessions, stale, error_code="QY_EXECUTION_ERROR", error_message="旧代失败"
        )
        assert not ok, "旧代失败发布必须被拒绝"

        with sessions() as session:
            row = session.execute(
                text(
                    "SELECT state, worker_id, generation, row_count, error_code "
                    "FROM query_runs WHERE id = :rid"
                ),
                {"rid": run_id},
            ).fetchone()
            snap = session.execute(
                text("SELECT 1 FROM query_run_snapshots WHERE run_id = :rid"),
                {"rid": run_id},
            ).fetchone()
        assert row.state == "running", "旧执行者不得改变新执行者的状态"
        assert row.worker_id == "it-worker-b", "旧执行者不得覆盖新执行者"
        assert row.generation == fresh.generation
        assert row.row_count is None and row.error_code is None
        assert snap is None, "被拒绝的发布不得留下快照"
    finally:
        _finalize_all(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_renew_lease_requires_current_ownership():
    """续期要求 worker + generation + running 三者同时成立。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claims: list[Claim] = []
    try:
        with TestClient(create_app()) as client:
            run_id = _submit(client, "SELECT 4 AS x")
        claim = queue.claim_next(
            sessions, worker_id="it-worker-a", lease_seconds=5, run_id=run_id
        )
        claims.append(claim)

        assert queue.renew_lease(
            sessions, claim, worker_id="it-worker-a", lease_seconds=60
        )
        with sessions() as session:
            expires = session.execute(
                text("SELECT lease_expires_at FROM query_runs WHERE id = :rid"),
                {"rid": run_id},
            ).fetchone()
        assert expires.lease_expires_at is not None

        stale_claim = Claim(
            run_id=claim.run_id, sql=claim.sql, attempt=claim.attempt,
            generation=claim.generation - 1,
        )
        assert not queue.renew_lease(
            sessions, stale_claim, worker_id="it-worker-a", lease_seconds=60
        ), "代过期续期必须被拒绝"
        assert not queue.renew_lease(
            sessions, claim, worker_id="it-worker-b", lease_seconds=60
        ), "非持有者续期必须被拒绝"
    finally:
        _finalize_all(sessions, claims)
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
