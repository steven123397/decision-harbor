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

from decisionharbor.config import WorkerSettings
from decisionharbor.dataset import load_dataset
from decisionharbor.domain import QueryResult
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.readiness import AnalyticsReadinessProbe, PlatformReadinessProbe
from decisionharbor.snapshots import SnapshotTooLarge, build_snapshot


logger = logging.getLogger("decisionharbor.worker")

# 全局容量判定串行化的咨询锁键：领取事务内计数与插入必须一致。
CAPACITY_ADVISORY_LOCK_KEY = 918_273_645

# 与 audit 侧一致的时长口径：finished_at 与 created_at 之差。
_DURATION_MS_SQL = "GREATEST(0, (EXTRACT(EPOCH FROM (now() - created_at)) * 1000)::integer)"


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
        self._worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:8]}"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def claim_next(self) -> ClaimedRun | None:
        """领取一个 queued 运行；全局有效执行所有权达到上限时返回 None。"""
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
                    SELECT id, raw_sql, statement_timeout_ms, max_rows
                    FROM query_runs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
            ).one_or_none()
            if row is None:
                return None
            run_id = str(row.id)
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
                    WHERE id = CAST(:run_id AS uuid) AND status = 'queued'
                    """
                ),
                {"attempt_id": attempt.id, "run_id": run_id},
            )
            if updated.rowcount != 1:
                raise RuntimeError("claimed query run changed state during claim")
            return ClaimedRun(
                run_id=run_id,
                attempt_id=attempt.id,
                generation=attempt.generation,
                raw_sql=row.raw_sql,
                statement_timeout_ms=row.statement_timeout_ms,
                max_rows=row.max_rows,
            )

    def publish_success(self, claimed: ClaimedRun, result: QueryResult) -> bool:
        """在一个事务内原子保存 succeeded 终态与结果快照；被栅栏时返回 False。"""
        try:
            snapshot = build_snapshot(result, max_rows=claimed.max_rows)
        except SnapshotTooLarge:
            return self.publish_failure(
                claimed,
                "result_too_large",
                "The query result exceeds the supported size limit.",
            )
        with self._platform.connect() as connection:
            fenced = False
            with connection.begin() as transaction:
                updated = connection.execute(
                    text(
                        f"""
                        UPDATE query_runs
                        SET status = 'succeeded',
                            returned_row_count = :row_count,
                            result_truncated = :truncated,
                            finished_at = now(),
                            duration_ms = {_DURATION_MS_SQL}
                        WHERE id = CAST(:run_id AS uuid)
                          AND status = 'running'
                          AND current_attempt_id = :attempt_id
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
                    UPDATE query_runs
                    SET status = 'failed',
                        error_code = :code,
                        error_summary = :summary,
                        finished_at = now(),
                        duration_ms = {_DURATION_MS_SQL}
                    WHERE id = CAST(:run_id AS uuid)
                      AND status = 'running'
                      AND current_attempt_id = :attempt_id
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

    def run_once(self) -> bool:
        """领取并完整处理一个运行；未领取到任何运行时返回 False。"""
        claimed = self.claim_next()
        if claimed is None:
            return False
        self.process(claimed)
        return True

    def process(self, claimed: ClaimedRun) -> None:
        logger.info("executing run %s attempt %s", claimed.run_id, claimed.generation)
        try:
            result = self._executor.execute(
                claimed.raw_sql,
                claimed.statement_timeout_ms,
                claimed.max_rows,
            )
        except ExecutionFailure as exc:
            published = self.publish_failure(claimed, exc.code, exc.message)
            logger.info("run %s failure published=%s code=%s", claimed.run_id, published, exc.code)
            return
        except Exception:
            published = self.publish_failure(claimed, "internal_error", "The query could not be completed.")
            logger.info("run %s failure published=%s code=internal_error", claimed.run_id, published)
            return
        published = self.publish_success(claimed, result)
        logger.info("run %s published=%s", claimed.run_id, published)

    def run_forever(self) -> None:
        stop = threading.Event()

        def heartbeat_loop() -> None:
            while not stop.wait(self._settings.heartbeat_ms / 1_000):
                try:
                    self.renew_leases()
                except Exception:
                    logger.warning("lease renewal failed", exc_info=True)

        heartbeat = threading.Thread(target=heartbeat_loop, name="worker-heartbeat", daemon=True)
        heartbeat.start()
        logger.info("worker %s started", self._worker_id)
        try:
            while True:
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
