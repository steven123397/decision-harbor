"""独立 Worker：从持久队列领取查询运行，用 analytics 只读身份执行并原子发布终态。"""

from collections.abc import Callable
from dataclasses import dataclass
import logging
import socket
import sys
import threading
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
import uvicorn

from decisionharbor.cleanup import ResultRetentionCleaner
from decisionharbor.config import WorkerSettings
from decisionharbor.dataset import load_dataset
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.readiness import AnalyticsReadinessProbe, PlatformReadinessProbe
from decisionharbor.snapshots import BuiltSnapshot


logger = logging.getLogger("decisionharbor.worker")

# 全局容量判定串行化的咨询锁键：领取事务内计数与插入必须一致。
CAPACITY_ADVISORY_LOCK_KEY = 918_273_645

# 与 audit 侧一致的时长口径：finished_at 与 created_at 之差。
_DURATION_MS_SQL = "GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)"

# 发布栅栏：只有当前 generation 且租约未过期的所有权才能发布状态或结果。
_OWNERSHIP_GUARD_SQL = """
  AND r.status = 'running'
  AND r.current_attempt_id = attempt.id
  AND attempt.id = :attempt_id
  AND attempt.lease_expires_at > now()
"""

_PUBLISH_FENCE_SQL = f"""
FROM execution_attempts AS attempt
WHERE r.id = CAST(:run_id AS uuid){_OWNERSHIP_GUARD_SQL}
"""

# cancelled 收敛的公共骨架：目标运行处于 cancelling 且所有权仍指向该尝试。
_CANCELLING_TARGET_SQL = """
FROM execution_attempts AS attempt
WHERE r.status = 'cancelling'
  AND r.current_attempt_id = attempt.id
"""

# failed 终态的统一字段事实：发布失败与尝试耗尽收敛共用。
_FAILED_SET_SQL = f"""
SET status = 'failed',
    error_code = :code,
    error_summary = :summary,
    finished_at = now(),
    duration_ms = {_DURATION_MS_SQL}
"""

# cancelled 终态的统一字段事实：当前所有者收敛与失联清理共用。
_CANCELLED_SET_SQL = f"""
SET status = 'cancelled',
    finished_at = now(),
    duration_ms = {_DURATION_MS_SQL}
"""

# 自动尝试只覆盖基础设施故障：analytics 连接或会话中断；其余错误码直接进入 failed 终态。
RETRYABLE_FAILURE_CODES = frozenset({"analytics_unavailable"})

_ATTEMPTS_EXHAUSTED_CODE = "execution_attempts_exhausted"
_ATTEMPTS_EXHAUSTED_SUMMARY = "The query did not complete within its execution attempt limit."


def attempt_cap_reached(generation: int, max_execution_attempts: int) -> bool:
    """尝试上限判定：generation 即该运行已创建的第 N 次执行尝试。"""
    return generation >= max_execution_attempts


def should_release_for_retry(code: str, generation: int, max_execution_attempts: int) -> bool:
    """纯决策接缝：该失败是否应把运行释放回队列等待自动重试。"""
    return code in RETRYABLE_FAILURE_CODES and not attempt_cap_reached(generation, max_execution_attempts)


@dataclass(frozen=True)
class ClaimedRun:
    run_id: str
    attempt_id: int
    generation: int
    raw_sql: str
    statement_timeout_ms: int
    max_rows: int


class QueryWorker:
    def __init__(self, settings: WorkerSettings, *, worker_id: str | None = None) -> None:
        self._settings = settings
        self._platform: Engine = create_engine(
            settings.platform_database_url, pool_size=5, max_overflow=0, pool_pre_ping=True
        )
        self._executor = PostgresQueryExecutor(settings.analytics_database_url, settings.max_concurrency)
        self._cleaner = ResultRetentionCleaner(self._platform)
        self._worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:8]}"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def claim_next(self) -> ClaimedRun | None:
        """领取一个 queued 运行或租约已过期的 running 运行；全局有效执行所有权达到上限时返回 None。"""
        with self._platform.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": CAPACITY_ADVISORY_LOCK_KEY}
            )
            owned = connection.execute(
                text(
                    """
                    SELECT count(*) FROM execution_attempts AS attempt
                    JOIN query_runs AS r ON r.id = attempt.run_id
                    WHERE attempt.finished_at IS NULL
                      AND attempt.lease_expires_at > now()
                      AND r.current_attempt_id = attempt.id
                      AND r.status IN ('running', 'cancelling')
                    """
                )
            ).scalar_one()
            if owned >= self._settings.max_concurrency:
                return None
            row = connection.execute(
                text(
                    """
                    SELECT r.id, r.raw_sql, r.statement_timeout_ms, r.max_rows,
                           attempt.id AS expired_attempt_id, attempt.generation AS expired_generation
                    FROM query_runs AS r
                    LEFT JOIN execution_attempts AS attempt ON attempt.id = r.current_attempt_id
                    WHERE r.status = 'queued'
                       OR (r.status = 'running' AND attempt.lease_expires_at <= now())
                    ORDER BY r.created_at, r.id
                    FOR UPDATE OF r SKIP LOCKED
                    LIMIT 1
                    """
                )
            ).one_or_none()
            if row is None:
                return None
            run_id = str(row.id)
            if row.expired_attempt_id is not None and attempt_cap_reached(
                row.expired_generation, self._settings.max_execution_attempts
            ):
                # 租约失联耗尽尝试：收敛为稳定 failed 终态，不再创建新执行尝试。
                exhausted = connection.execute(
                    text(
                        f"""
                        UPDATE query_runs AS r
                        {_FAILED_SET_SQL}
                        WHERE r.id = CAST(:run_id AS uuid)
                          AND r.status = 'running'
                          AND r.current_attempt_id = :attempt_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "attempt_id": row.expired_attempt_id,
                        "code": _ATTEMPTS_EXHAUSTED_CODE,
                        "summary": _ATTEMPTS_EXHAUSTED_SUMMARY,
                    },
                ).rowcount
                if exhausted == 1:
                    self._finish_attempt(connection, row.expired_attempt_id)
                return None
            attempt = connection.execute(
                text(
                    """
                    INSERT INTO execution_attempts (run_id, generation, worker_id, claimed_at, lease_expires_at)
                    SELECT CAST(:run_id AS uuid),
                           COALESCE(
                               (SELECT max(generation) FROM execution_attempts WHERE run_id = CAST(:run_id AS uuid)),
                               0
                           ) + 1,
                           :worker_id,
                           now(),
                           now() + (:lease_ms * interval '1 millisecond')
                    RETURNING id, generation
                    """
                ),
                {"run_id": run_id, "worker_id": self._worker_id, "lease_ms": self._settings.lease_ms},
            ).one()
            updated = connection.execute(
                text(
                    """
                    UPDATE query_runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, now()),
                        current_attempt_id = :attempt_id
                    WHERE id = CAST(:run_id AS uuid) AND status IN ('queued', 'running')
                    """
                ),
                {"attempt_id": attempt.id, "run_id": run_id},
            )
            if updated.rowcount != 1:
                raise RuntimeError("claimed query run changed state during claim")
            if row.expired_attempt_id is not None:
                # 接管失联所有权：终结已失租的旧尝试；运行保持 running，不回退 queued。
                self._finish_attempt(connection, row.expired_attempt_id)
            return ClaimedRun(
                run_id=run_id,
                attempt_id=attempt.id,
                generation=attempt.generation,
                raw_sql=row.raw_sql,
                statement_timeout_ms=row.statement_timeout_ms,
                max_rows=row.max_rows,
            )

    def publish_success(self, claimed: ClaimedRun, snapshot: BuiltSnapshot) -> bool:
        """在一个事务内原子保存 succeeded 终态与结果快照；被栅栏时返回 False。"""
        with self._platform.connect() as connection:
            fenced = False
            with connection.begin() as transaction:
                updated = connection.execute(
                    text(
                        f"""
                        UPDATE query_runs AS r
                        SET status = 'succeeded',
                            returned_row_count = :row_count,
                            result_truncated = :truncated,
                            finished_at = now(),
                            duration_ms = {_DURATION_MS_SQL}
                        {_PUBLISH_FENCE_SQL}
                        """
                    ),
                    {
                        "run_id": claimed.run_id,
                        "attempt_id": claimed.attempt_id,
                        "row_count": snapshot.row_count,
                        "truncated": snapshot.truncated,
                    },
                ).rowcount
                if updated != 1:
                    transaction.rollback()
                    fenced = True
                else:
                    connection.execute(
                        text(
                            """
                            INSERT INTO result_snapshots (run_id, payload, truncated, row_count, byte_size, created_at)
                            VALUES (CAST(:run_id AS uuid), :payload, :truncated, :row_count, :byte_size, now())
                            """
                        ),
                        {
                            "run_id": claimed.run_id,
                            "payload": snapshot.payload,
                            "truncated": snapshot.truncated,
                            "row_count": snapshot.row_count,
                            "byte_size": snapshot.byte_size,
                        },
                    )
                    self._finish_attempt(connection, claimed.attempt_id)
        if fenced:
            # 旧 generation 不能发布运行状态或结果，但本次执行尝试必须终结：
            # 否则心跳与容量统计会把失联所有权永久计入。
            with self._platform.begin() as connection:
                self._finish_attempt(connection, claimed.attempt_id)
            return False
        return True

    def publish_failure(self, claimed: ClaimedRun, code: str, message: str) -> bool:
        with self._platform.begin() as connection:
            updated = connection.execute(
                text(
                    f"""
                    UPDATE query_runs AS r
                    {_FAILED_SET_SQL}
                    {_PUBLISH_FENCE_SQL}
                    """
                ),
                {"run_id": claimed.run_id, "attempt_id": claimed.attempt_id, "code": code, "summary": message},
            ).rowcount
            self._finish_attempt(connection, claimed.attempt_id)
        return updated == 1

    def renew_leases(self) -> None:
        """为本人持有的当前有效执行所有权续租；已过期或已失栅的所有权不复活。"""
        with self._platform.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE execution_attempts AS attempt
                    SET lease_expires_at = now() + (:lease_ms * interval '1 millisecond')
                    FROM query_runs AS r
                    WHERE attempt.worker_id = :worker_id
                      AND attempt.finished_at IS NULL
                      AND attempt.lease_expires_at > now()
                      AND r.id = attempt.run_id
                      AND r.current_attempt_id = attempt.id
                      AND r.status IN ('running', 'cancelling')
                    """
                ),
                {"worker_id": self._worker_id, "lease_ms": self._settings.lease_ms},
            )

    def request_pending_cancellations(self) -> int:
        """对本人仍持有的 cancelling 运行 best effort 请求数据库取消；返回发现数。

        取消失败不撤销已持久化的取消意图；数据库活动的最终停止仍由
        statement_timeout 与所有者收敛路径兜底。
        """
        with self._platform.connect() as connection:
            pending = list(
                connection.execute(
                    text(
                        """
                        SELECT CAST(r.id AS text)
                        FROM query_runs AS r
                        JOIN execution_attempts AS attempt ON attempt.id = r.current_attempt_id
                        WHERE r.status = 'cancelling'
                          AND attempt.worker_id = :worker_id
                          AND attempt.finished_at IS NULL
                        """
                    ),
                    {"worker_id": self._worker_id},
                ).scalars()
            )
        for run_id in pending:
            try:
                requested = self._executor.cancel_active(run_id)
            except Exception:
                # 取消是 best effort：单次失败不撤销取消意图，也不中断其余维护。
                logger.warning("database cancellation request failed for run %s", run_id, exc_info=True)
                continue
            if requested:
                logger.info("database cancellation requested for run %s", run_id)
        return len(pending)

    def converge_expired_cancellations(self) -> int:
        """接管/清理循环收敛：把当前所有权租约已过期的 cancelling 运行收敛为 cancelled。

        幂等：并发执行时同一运行只有一方赢得条件更新；收敛不创建新执行尝试。
        """
        with self._platform.begin() as connection:
            attempt_ids = list(
                connection.execute(
                    text(
                        f"""
                        UPDATE query_runs AS r
                        {_CANCELLED_SET_SQL}
                        {_CANCELLING_TARGET_SQL}
                          AND attempt.lease_expires_at <= now()
                        RETURNING attempt.id
                        """
                    )
                ).scalars()
            )
            for attempt_id in attempt_ids:
                self._finish_attempt(connection, attempt_id)
        if attempt_ids:
            logger.info("converged %s cancelling run(s) after lease expiry", len(attempt_ids))
        return len(attempt_ids)

    def run_once(self) -> bool:
        """收敛失联取消后领取并完整处理一个运行；未领取到任何运行时返回 False。"""
        self.converge_expired_cancellations()
        claimed = self.claim_next()
        if claimed is None:
            return False
        self.process(claimed)
        return True

    def process(self, claimed: ClaimedRun) -> None:
        logger.info("executing run %s attempt %s", claimed.run_id, claimed.generation)
        try:
            snapshot = self._executor.execute(
                claimed.raw_sql,
                claimed.statement_timeout_ms,
                claimed.max_rows,
                cancel_key=claimed.run_id,
            )
        except ExecutionFailure as exc:
            if should_release_for_retry(
                exc.code, claimed.generation, self._settings.max_execution_attempts
            ) and self._release_for_retry(claimed):
                logger.info(
                    "run %s attempt %s released for retry after %s",
                    claimed.run_id,
                    claimed.generation,
                    exc.code,
                )
                return
            published = self.publish_failure(claimed, exc.code, exc.message)
            logger.info("run %s failure published=%s code=%s", claimed.run_id, published, exc.code)
        except Exception:
            published = self.publish_failure(claimed, "internal_error", "The query could not be completed.")
            logger.info("run %s failure published=%s code=internal_error", claimed.run_id, published)
        else:
            published = self.publish_success(claimed, snapshot)
            logger.info("run %s published=%s", claimed.run_id, published)
        # 数据库工作已结束（结果可能已被取消栅栏丢弃）：把仍归本人所有的
        # cancelling 运行收敛为 cancelled；无取消意图时条件更新为无操作。
        self._converge_cancelled(claimed)

    def _converge_cancelled(self, claimed: ClaimedRun) -> bool:
        """当前所有者收敛：仍归本人有效持有的 cancelling 运行进入 cancelled 终态。"""
        with self._platform.begin() as connection:
            converged = connection.execute(
                text(
                    f"""
                    UPDATE query_runs AS r
                    {_CANCELLED_SET_SQL}
                    {_CANCELLING_TARGET_SQL}
                      AND r.id = CAST(:run_id AS uuid)
                      AND attempt.id = :attempt_id
                      AND attempt.lease_expires_at > now()
                    """
                ),
                {"run_id": claimed.run_id, "attempt_id": claimed.attempt_id},
            ).rowcount
            if converged:
                self._finish_attempt(connection, claimed.attempt_id)
        if converged:
            logger.info("run %s converged to cancelled after database work ended", claimed.run_id)
        return converged == 1

    def _release_for_retry(self, claimed: ClaimedRun) -> bool:
        """把仍归本人所有的运行释放回可领取状态：终结当前尝试并使租约即刻过期。"""
        with self._platform.begin() as connection:
            released = connection.execute(
                text(
                    f"""
                    UPDATE execution_attempts AS attempt
                    SET finished_at = now(),
                        lease_expires_at = now()
                    FROM query_runs AS r
                    WHERE r.id = CAST(:run_id AS uuid){_OWNERSHIP_GUARD_SQL}
                      AND attempt.finished_at IS NULL
                    """
                ),
                {"run_id": claimed.run_id, "attempt_id": claimed.attempt_id},
            ).rowcount
        return released == 1

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()

        def heartbeat_loop() -> None:
            while not stop.wait(self._settings.heartbeat_ms / 1_000):
                try:
                    self.renew_leases()
                except Exception:
                    logger.warning("lease renewal failed", exc_info=True)
                # 心跳线程独立于主循环：执行占用主循环时仍按心跳周期
                # 请求取消并收敛失联的 cancelling 运行。
                try:
                    self.request_pending_cancellations()
                    self.converge_expired_cancellations()
                except Exception:
                    logger.warning("cancellation maintenance failed", exc_info=True)

        def cleanup_loop() -> None:
            while not stop.wait(self._settings.cleanup_interval_ms / 1_000):
                try:
                    removed = self._cleaner.cleanup_once()
                    if removed:
                        logger.info("retention cleanup removed %s expired snapshots", removed)
                except Exception:
                    logger.warning("retention cleanup failed", exc_info=True)

        heartbeat = threading.Thread(target=heartbeat_loop, name="worker-heartbeat", daemon=True)
        cleanup = threading.Thread(target=cleanup_loop, name="worker-cleanup", daemon=True)
        heartbeat.start()
        cleanup.start()
        logger.info("worker %s started", self._worker_id)
        try:
            while not stop.is_set():
                try:
                    busy = self.run_once()
                except Exception:
                    logger.warning("claim failed", exc_info=True)
                    busy = False
                if not busy:
                    stop.wait(self._settings.poll_ms / 1_000)
        finally:
            stop.set()

    @staticmethod
    def _finish_attempt(connection: Connection, attempt_id: int) -> None:
        connection.execute(
            text(
                "UPDATE execution_attempts SET finished_at = now() WHERE id = :attempt_id AND finished_at IS NULL"
            ),
            {"attempt_id": attempt_id},
        )


def create_worker_ready_app(readiness_check: Callable[[], bool]) -> FastAPI:
    app = FastAPI(title="DecisionHarbor Worker", version="1.0.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"data": {"status": "ok"}, "error": None}

    @app.get("/ready", response_model=None)
    def ready():
        if not readiness_check():
            return JSONResponse(
                {"data": None, "error": {"code": "service_not_ready", "message": "The service is not ready."}},
                status_code=503,
            )
        return {"data": {"status": "ready"}, "error": None}

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    try:
        settings = WorkerSettings.from_env()
    except KeyError as exc:
        print(f"worker configuration error: missing environment variable {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except ValueError as exc:
        print(f"worker configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    dataset = load_dataset(settings.dataset_root)
    platform_readiness = PlatformReadinessProbe(settings.platform_database_url)
    analytics_readiness = AnalyticsReadinessProbe(settings.analytics_readiness_database_url)
    app = create_worker_ready_app(
        lambda: platform_readiness.check_ready() and analytics_readiness.check_ready(dataset)
    )
    server = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=settings.http_port, log_level="warning"),
        name="worker-readiness",
        daemon=True,
    )
    server.start()
    QueryWorker(settings).run_forever()


if __name__ == "__main__":
    main()
