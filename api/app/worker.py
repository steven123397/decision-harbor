"""后台执行组件入口：认领 queued 运行，以只读身份执行，原子发布终态+快照。

多实例协同：SKIP LOCKED 认领互不重复，租约过期后被其他执行者接管
（ADR-0017）；执行期间由守护线程心跳续期，健康执行者的长查询不会
因租约到期被误接管。

容量与重试（ADR-0019）：认领携带全局并发闸门（数据库有效租约数为
唯一事实源，跨实例生效）；本进程并发持有多个认领，认领时排除自己在
执行的运行（自接管排除）；基础设施类失败经 queue.requeue_or_fail 回队
重跑，attempt 硬上界 3；租约过期且耗尽的运行由 keeper 周期清扫终态。

取消协同（#12）：续期要求 running，取消请求把运行转入 cancelling 后
续期自然失败；keeper 据此识别取消并对执行连接调用 cancel()（best
effort 中止底层查询），执行线程的处置被 fencing 拒绝时以
finalize_cancelled 收尾；执行者在收尾前崩溃由过期清扫兜底。

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
from concurrent.futures import ThreadPoolExecutor

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
from app.runs.models import STATE_CANCELLING
from app.runs.queue import Claim

logger = logging.getLogger("decision_harbor.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = os.environ.get("WORKER_ID") or f"worker-{uuid.uuid4().hex[:8]}"

    engine = create_platform_engine(settings.platform_app_url)
    session_factory = create_platform_session_factory(engine)
    # 本地并发与执行线程池对齐：一次最多同时执行 worker_max_concurrency 条。
    pool = ReadOnlyPool(
        settings.analytics_readonly_dsn,
        max_size=settings.worker_max_concurrency,
        statement_timeout_ms=settings.query_statement_timeout_ms,
    )

    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    logger.info(
        "worker %s 启动（本地并发 %s，全局并发 %s）",
        worker_id,
        settings.worker_max_concurrency,
        settings.global_query_concurrency,
    )

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

    # 本进程在执行的认领：run_id → Claim。主线程写（认领/完成），keeper
    # 读并清理已失去所有权的项；完成回调也会写——统一由锁保护。
    active: dict[int, Claim] = {}
    # 各认领当前占用的执行连接：keeper 取消 cancelling 运行的底层
    # 查询用（conn.cancel() 线程安全）；执行线程 acquire/release 时写。
    conns: dict[int, object] = {}
    active_lock = threading.Lock()

    def release_active(claim: Claim) -> None:
        with active_lock:
            if active.get(claim.run_id) is claim:
                del active[claim.run_id]
            conns.pop(claim.run_id, None)

    def run_state(run_id: int) -> str | None:
        with session_factory() as session:
            return session.execute(
                text("SELECT state FROM query_runs WHERE id = :rid"), {"rid": run_id}
            ).scalar_one_or_none()

    def keeper() -> None:
        """心跳 + 活动租约续期 + 取消协同 + 过期清扫。执行线程会阻塞在
        查询上，续期必须独立于执行路径，否则长查询期间租约必然过期。"""
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
                with active_lock:
                    claims = list(active.items())
                for run_id, claim in claims:
                    if queue.renew_lease(
                        session_factory,
                        claim,
                        worker_id=worker_id,
                        lease_seconds=settings.worker_lease_seconds,
                    ):
                        continue
                    # 续期被拒：running 不再成立。取消请求转入 cancelling
                    # 是预期内的形态——best effort 中止底层查询，执行线程
                    # 的处置会因 fencing 失败，转由 finalize_cancelled 收尾。
                    if run_state(run_id) == STATE_CANCELLING:
                        with active_lock:
                            conn = conns.get(run_id)
                        if conn is not None:
                            try:
                                conn.cancel()
                                logger.info("运行 #%s 取消已受理，中止底层查询", run_id)
                            except Exception:
                                logger.exception("运行 #%s 底层查询中止失败，等待语句超时兜底", run_id)
                        continue
                    # 其余形态（被接管、已终态）：停止续期，结果处置自会被
                    # fencing 拒绝。不在此移出 active——执行线程仍压着该
                    # 运行，留在排除集合直到执行线程收尾，避免本进程把
                    # 刚丢掉的运行抢回与自己的旧执行竞态。
                    logger.warning(
                        "运行 #%s 租约续期被拒绝（已失去所有权）", run_id
                    )
                # 崩溃执行者留下的过期且耗尽运行没有认领路径可走，
                # 只能由清扫补上终态（幂等，通常 0 行）。宽限取一个
                # 租约周期：给续期抖动的执行者留复活窗口（ADR-0019）。
                queue.fail_expired_exhausted(
                    session_factory, grace_seconds=settings.worker_lease_seconds
                )
                # 同理清 cancelling 的遗孤：执行者登记取消后崩溃，
                # 行永远停在 cancelling——补上 cancelled 终态。
                queue.finalize_expired_cancelling(
                    session_factory, grace_seconds=settings.worker_lease_seconds
                )
            except Exception:
                # 单次心跳失败不退出：租约到期前仍有后续续期机会。
                logger.exception("keeper 心跳/续期失败")

    threading.Thread(target=keeper, name="keeper", daemon=True).start()

    executor = ThreadPoolExecutor(
        max_workers=settings.worker_max_concurrency, thread_name_prefix="exec"
    )

    def run(claim: Claim) -> None:
        try:
            _run_claim(session_factory, pool, settings, claim, conns, active_lock)
        finally:
            release_active(claim)

    try:
        while not stop.is_set():
            if queue.worker_paused(session_factory):
                stop.wait(settings.worker_poll_interval_ms / 1000)
                continue
            with active_lock:
                slots = settings.worker_max_concurrency - len(active)
                # 自接管排除：不认领自己仍在执行的运行（即使其租约已
                # 过期——那是本进程的执行线程还压着它，抢回只会自相竞态）。
                exclude = set(active)
            claimed = 0
            while claimed < slots:
                claim = queue.claim_next(
                    session_factory,
                    worker_id=worker_id,
                    lease_seconds=settings.worker_lease_seconds,
                    capacity=settings.global_query_concurrency,
                    exclude_run_ids=exclude,
                )
                if claim is None:
                    break
                claimed += 1
                with active_lock:
                    active[claim.run_id] = claim
                executor.submit(run, claim)
            if claimed == 0:
                # 队列空、全局容量满或本地并发满：按轮询间隔空转。
                stop.wait(settings.worker_poll_interval_ms / 1000)
    finally:
        pool.close()
        engine.dispose()
        # 等在执行的运行走完处置路径再退出；语句超时上界（10s）内会
        # 自然结束，超时由 docker 的 SIGKILL 与接管路径兜底。
        executor.shutdown(wait=True)
        logger.info("worker %s 退出", worker_id)


def _run_claim(session_factory, pool, settings, claim: Claim, conns, active_lock) -> None:
    logger.info("认领运行 #%s（attempt=%s）", claim.run_id, claim.attempt)
    try:
        conn = pool.acquire()
        with active_lock:
            conns[claim.run_id] = conn
        try:
            result = execute_readonly(claim.sql, conn=conn, max_rows=settings.query_max_rows)
        finally:
            with active_lock:
                conns.pop(claim.run_id, None)
            pool.release(conn)
    except ExecutionFailure as exc:
        _handle_failure(session_factory, claim, exc)
        return
    except Exception:
        logger.exception("运行 #%s 执行异常", claim.run_id)
        _handle_failure(
            session_factory,
            claim,
            ExecutionFailure(QY_EXECUTION_ERROR, "查询执行失败，请稍后重试"),
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
        # 代过期：所有权已不再成立，本次结果按 fencing 约定丢弃；若
        # 原因是取消获胜（cancelling），由本执行者收尾终态。
        _finalize_if_cancelled(session_factory, claim, "发布被 fencing 拒绝，放弃结果")
        return
    logger.info("运行 #%s 发布成功（%s 行）", claim.run_id, result.row_count)


def _handle_failure(session_factory, claim: Claim, exc: ExecutionFailure) -> None:
    """失败处置走 requeue_or_fail：基础设施类失败自动回队重跑，其余
    直接终态。未捕获异常按确定性失败处理（保守面：不自动重跑未知故障）。
    处置被 fencing 拒绝时尝试取消收尾——取消引发的底层中止以执行失败
    形态到达这里，此时运行在 cancelling，正是本执行者该落终态的路径。
    """
    outcome = queue.requeue_or_fail(
        session_factory, claim, error_code=exc.code, error_message=exc.message
    )
    if outcome == "fenced":
        _finalize_if_cancelled(session_factory, claim, "失败处置被 fencing 拒绝")
    elif outcome == "requeued":
        logger.warning(
            "运行 #%s 基础设施失败（%s），已回队等待第 %s 次执行",
            claim.run_id,
            exc.code,
            claim.attempt + 1,
        )
    else:
        logger.info("运行 #%s 发布失败（%s）", claim.run_id, exc.code)


def _finalize_if_cancelled(session_factory, claim: Claim, fenced_reason: str) -> None:
    """fencing 拒绝后的收尾：取消获胜则落 cancelled 终态，否则只记日志。"""
    if queue.finalize_cancelled(session_factory, claim):
        logger.info("运行 #%s 取消获胜，落 cancelled 终态", claim.run_id)
    else:
        logger.warning("运行 #%s %s", claim.run_id, fenced_reason)


if __name__ == "__main__":
    main()
