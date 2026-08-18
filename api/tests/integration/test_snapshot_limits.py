"""快照上限与截断语义测试（#13）：500 行 + 1 MiB 统一约束取数与快照。

行数或字节任一达限即停止取数并显式标记 truncated；单行自身超过上限
以稳定错误 QY_ROW_TOO_LARGE 落 failed（不在自动重跑集合，ADR-0011 扩展）。
"""

from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.db import create_platform_engine, create_platform_session_factory
from app.execute.executor import (
    QY_ROW_TOO_LARGE,
    ExecutionFailure,
    execute_readonly,
    snapshot_json,
)
from app.main import create_app
from app.runs import queue
from app.runs.queue import REQUEUE_ELIGIBLE_ERROR_CODES

READONLY_DSN = os.environ["ANALYTICS_READONLY_DSN"]
PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]

# 单行超限用例的 SQL：白名单内（concat）以递归 CTE 倍增字符串，
# 17 步 × 18 字符种子 ≈ 2.36 MB 单行——超 1 MiB 上限且 SQL 本身极短。
WIDE_ROW_SQL = (
    "WITH RECURSIVE t(s, step) AS ("
    "(SELECT (SELECT concat(max(display_name)) FROM customers), 0) "
    "UNION ALL "
    "SELECT concat(t.s, t.s), step + 1 FROM t WHERE step < 17) "
    "SELECT s FROM t ORDER BY step DESC LIMIT 1"
)


def _sessions():
    engine = create_platform_engine(PLATFORM_APP_URL)
    return create_platform_session_factory(engine), engine


def test_rows_over_limit_truncate_with_actual_count():
    """超过 500 行的查询：恰好 500 行 + truncated=true + 实际行数。"""
    with psycopg.connect(READONLY_DSN) as conn:
        result = execute_readonly(
            "SELECT id, order_id, product_id, quantity, unit_price "
            "FROM order_items ORDER BY id",
            conn=conn,
            max_rows=500,
        )
    assert result.row_count == 500
    assert len(result.rows) == 500
    assert result.truncated is True


def test_truncation_surfaces_through_snapshot_end_to_end():
    """500 行截断经 HTTP 透出：result 的 row_count/truncated 与快照落库
    的 size_bytes（不超过 1 MiB）一致（验收 1+2 的端到端）。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claim = None
    try:
        with TestClient(create_app()) as client:
            sql = (
                "SELECT id, order_id, product_id, quantity, unit_price "
                "FROM order_items ORDER BY id"
            )
            resp = client.post("/api/v1/query-runs", json={"sql": sql})
            assert resp.status_code == 202
            run_id = resp.json()["run"]["id"]

        claim = queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        )
        with psycopg.connect(READONLY_DSN) as conn:
            result = execute_readonly(sql, conn=conn, max_rows=500)
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
        assert snap["size_bytes"] <= 1_048_576, "快照序列化不得超过 1 MiB"

        with TestClient(create_app()) as client:
            body = client.get(f"/api/v1/query-runs/{run_id}/result").json()["result"]
        assert body["row_count"] == 500
        assert body["truncated"] is True
        assert len(body["rows"]) == 500
    finally:
        if claim is not None:
            queue.publish_failure(
                sessions, claim, error_code="QY_TEST_CLEANUP", error_message="收尾"
            )
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()


def test_exact_limit_not_truncated():
    """恰好等于上限的行数不是截断结果（ADR-0011：截断只表示超限）。"""
    with psycopg.connect(READONLY_DSN) as conn:
        result = execute_readonly(
            "SELECT id FROM customers ORDER BY id",  # 固定数据集 100 行
            conn=conn,
            max_rows=100,
        )
    assert result.row_count == 100
    assert result.truncated is False


def test_byte_limit_truncates_before_1mib():
    """字节上限与行数上限并行约束：行数未达 500 但累计字节达 1 MiB
    即停止取数并标记 truncated。repeat 不在函数白名单，经订单明细
    自关联放大行宽构造大结果集。"""
    with psycopg.connect(READONLY_DSN) as conn:
        result = execute_readonly(
            "SELECT a.id AS a_id, b.id AS b_id, a.unit_price, b.unit_price "
            "FROM order_items a JOIN order_items b ON a.order_id = b.order_id "
            "ORDER BY a.id, b.id",
            conn=conn,
            max_rows=500,
        )
    assert result.truncated is True, "字节数达限必须标记截断"
    assert result.row_count <= 500
    serialized = len(
        snapshot_json({"rows": result.rows}).encode("utf-8")
    )
    # 达限即停：含结构预留后的序列化字节不超过 1 MiB
    assert serialized <= 1_048_576


def test_single_row_over_limit_fails_with_stable_code():
    """单行自身超过 1 MiB：稳定错误 QY_ROW_TOO_LARGE，不产生部分结果。"""
    with psycopg.connect(READONLY_DSN) as conn:
        # 先探宽：递归倍增后的单行必须确实超过 1 MiB（固定数据集种子 18 字符）
        with conn.cursor() as cur:
            cur.execute(f"SELECT length(s) FROM ({WIDE_ROW_SQL}) wide")
            wide = cur.fetchone()[0]
        assert wide > 1_048_576, f"测试数据构造不足（{wide} 字节）"
        conn.rollback()
        with pytest.raises(ExecutionFailure) as caught:
            execute_readonly(WIDE_ROW_SQL, conn=conn, max_rows=500)
    assert caught.value.code == QY_ROW_TOO_LARGE
    assert QY_ROW_TOO_LARGE not in REQUEUE_ELIGIBLE_ERROR_CODES, (
        "单行超限是确定性失败，不得自动重跑"
    )


def test_oversized_row_run_fails_without_retry_end_to_end():
    """单行超限的运行端到端：attempt 1 直接 failed，错误码稳定。"""
    sessions, engine = _sessions()
    queue.set_worker_paused(sessions, paused=True)
    claim = None
    try:
        with TestClient(create_app()) as client:
            resp = client.post("/api/v1/query-runs", json={"sql": WIDE_ROW_SQL})
            assert resp.status_code == 202
            run_id = resp.json()["run"]["id"]

        # 真实认领（generation/attempt 与库一致），走 worker 同款处置路径
        claim = queue.claim_next(
            sessions, worker_id="it-worker", lease_seconds=60, run_id=run_id
        )
        assert claim is not None and claim.attempt == 1
        with psycopg.connect(READONLY_DSN) as conn:
            try:
                execute_readonly(WIDE_ROW_SQL, conn=conn, max_rows=500)
                raise AssertionError("预期单行超限失败")
            except ExecutionFailure as exc:
                failure = exc
        outcome = queue.requeue_or_fail(
            sessions,
            claim,
            error_code=failure.code,
            error_message=failure.message,
        )
        assert outcome == "failed", "确定性失败必须直接终态"
        with TestClient(create_app()) as client:
            body = client.get(f"/api/v1/query-runs/{run_id}").json()["run"]
        assert body["error_code"] == QY_ROW_TOO_LARGE
        assert body["attempt"] == 1
    finally:
        if claim is not None:
            queue.publish_failure(
                sessions, claim, error_code="QY_TEST_CLEANUP", error_message="收尾"
            )
        queue.set_worker_paused(sessions, paused=False)
        engine.dispose()
