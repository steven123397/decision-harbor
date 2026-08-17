"""后台执行组件入口：认领 queued 运行，以只读身份执行，原子发布终态+快照。

单进程串行执行（一次一条）；多实例协同、租约接管与全局并发闸门在
后继工单（#10/#11）落地。治理已在受理时同步完成，worker 信任队列
里的 SQL 均通过策略判定，但仍只持分析只读身份执行。
"""

from __future__ import annotations

import logging
import os
import signal
import time
import uuid

from sqlalchemy import text

from app.config import get_settings
from app.db import create_platform_engine, create_platform_session_factory
from app.execute.executor import (
    QY_EXECUTION_ERROR,
    ExecutionFailure,
    execute_readonly,
)
from app.execute.pool import ReadOnlyPool
from app.runs import queue

logger = logging.getLogger("decision_harbor.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = os.environ.get("WORKER_ID") or f"worker-{uuid.uuid4().hex[:8]}"

    engine = create_platform_engine(settings.platform_app_url)
    session_factory = create_platform_session_factory(engine)
    # 串行执行一次只占一条连接；语句超时沿用治理配置。
    pool = ReadOnlyPool(
        settings.analytics_readonly_dsn,
        max_size=1,
        statement_timeout_ms=settings.query_statement_timeout_ms,
    )

    stop = False

    def request_stop(signum, frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    logger.info("worker %s 启动", worker_id)

    def beat() -> None:
        # 心跳写入 platform 库：healthcheck 与后续租约续期（#10）共用。
        with session_factory() as session:
            session.execute(
                text(
                    "INSERT INTO worker_hearts (worker_id, beat_at) VALUES (:w, now()) "
                    "ON CONFLICT (worker_id) DO UPDATE SET beat_at = now()"
                ),
                {"w": worker_id},
            )
            session.commit()

    last_beat = 0.0
    try:
        while not stop:
            now = time.monotonic()
            if now - last_beat >= settings.worker_heartbeat_seconds:
                beat()
                last_beat = now
            if queue.worker_paused(session_factory):
                time.sleep(settings.worker_poll_interval_ms / 1000)
                continue
            claim = queue.claim_next(
                session_factory,
                worker_id=worker_id,
                lease_seconds=settings.worker_lease_seconds,
            )
            if claim is None:
                time.sleep(settings.worker_poll_interval_ms / 1000)
                continue
            _run_claim(session_factory, pool, settings, claim)
    finally:
        pool.close()
        engine.dispose()
        logger.info("worker %s 退出", worker_id)


def _run_claim(session_factory, pool, settings, claim) -> None:
    logger.info("认领运行 #%s（attempt=%s）", claim.run_id, claim.attempt)
    try:
        conn = pool.acquire()
        try:
            result = execute_readonly(claim.sql, conn=conn, max_rows=settings.query_max_rows)
        finally:
            pool.release(conn)
    except ExecutionFailure as exc:
        _publish(session_factory, claim, error=(exc.code, exc.message))
        return
    except Exception:
        logger.exception("运行 #%s 执行异常", claim.run_id)
        _publish(
            session_factory,
            claim,
            error=(QY_EXECUTION_ERROR, "查询执行失败，请稍后重试"),
        )
        return

    columns_snapshot = queue.build_snapshot(result)
    ok = queue.publish_success(
        session_factory,
        claim,
        columns=columns_snapshot["columns"],
        rows=columns_snapshot["rows"],
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
        size_bytes=columns_snapshot["size_bytes"],
        retention_hours=settings.result_retention_hours,
    )
    if not ok:
        # 代过期：所有权已被接管，本次结果按 fencing 约定丢弃。
        logger.warning("运行 #%s 发布被 fencing 拒绝，放弃结果", claim.run_id)
        return
    logger.info("运行 #%s 发布成功（%s 行）", claim.run_id, result.row_count)


def _publish(session_factory, claim, *, error: tuple[str, str]) -> None:
    ok = queue.publish_failure(
        session_factory, claim, error_code=error[0], error_message=error[1]
    )
    if not ok:
        logger.warning("运行 #%s 失败发布被 fencing 拒绝", claim.run_id)
    else:
        logger.info("运行 #%s 发布失败（%s）", claim.run_id, error[0])


if __name__ == "__main__":
    main()
