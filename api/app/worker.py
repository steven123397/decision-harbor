"""后台执行组件入口：认领 queued 运行，以只读身份执行，原子发布终态+快照。

多实例协同：SKIP LOCKED 认领互不重复，租约过期后被其他执行者接管
（ADR-0017）；执行期间由守护线程心跳续期，健康执行者的长查询不会
因租约到期被误接管。全局并发闸门与自动重试在后继工单（#11）落地。
治理已在受理时同步完成，worker 信任队列里的 SQL 均通过策略判定，
但仍只持分析只读身份执行。
"""

from __future__ import annotations

import logging
import os
import signal
import threading
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

    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    logger.info("worker %s 启动", worker_id)

    def beat() -> None:
        # 心跳写入 platform 库：healthcheck 与租约续期共用（ADR-0016/0017）。
        with session_factory() as session:
            session.execute(
                text(
                    "INSERT INTO worker_hearts (worker_id, beat_at) VALUES (:w, now()) "
                    "ON CONFLICT (worker_id) DO UPDATE SET beat_at = now()"
                ),
                {"w": worker_id},
            )
            session.commit()

    # 当前执行中的认领；仅 keeper 线程读、主线程写（单槽引用赋值原子）。
    active: list[queue.Claim | None] = [None]

    def keeper() -> None:
        """心跳 + 活动租约续期。主线程串行执行会阻塞在查询上，
        续期必须独立于执行路径，否则长查询期间租约必然过期。"""
        interval = min(
            settings.worker_heartbeat_seconds,
            max(1, settings.worker_lease_seconds // 3),
        )
        while not stop.is_set():
            stop.wait(interval)
            if stop.is_set():
                return
            try:
                beat()
                claim = active[0]
                if claim is None:
                    continue
                if not queue.renew_lease(
                    session_factory,
                    claim,
                    worker_id=worker_id,
                    lease_seconds=settings.worker_lease_seconds,
                ):
                    # 所有权已被接管：停止续期，结果发布自会被 fencing 拒绝。
                    # 仅在槽内仍是该认领时清空——主线程可能已换入新认领。
                    logger.warning(
                        "运行 #%s 租约续期被拒绝（已失去所有权）", claim.run_id
                    )
                    if active[0] is claim:
                        active[0] = None
            except Exception:
                # 单次心跳失败不退出：租约到期前仍有后续续期机会。
                logger.exception("keeper 心跳/续期失败")

    threading.Thread(target=keeper, name="keeper", daemon=True).start()

    try:
        while not stop.is_set():
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
            active[0] = claim
            _run_claim(session_factory, pool, settings, claim)
            active[0] = None
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
