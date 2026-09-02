import signal
import socket
import logging
from time import monotonic
from threading import Event, Thread
from typing import Callable, Protocol
from uuid import uuid4

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import ExecutionOwnership, QueryResult, QueryRun
from decisionharbor.executor import ExecutionCancellation, ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository, StateConflict
from decisionharbor.retention import ResultRetention


LOGGER = logging.getLogger(__name__)


class WorkerRepository(Protocol):
    def claim_next(
        self,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
        max_execution_attempts: int,
    ) -> ExecutionOwnership | None: ...

    def renew_lease(
        self,
        ownership: ExecutionOwnership,
        lease_ms: int,
    ) -> ExecutionOwnership | None: ...

    def release_ownership(self, ownership: ExecutionOwnership) -> bool: ...

    def release_for_next_attempt(self, ownership: ExecutionOwnership) -> bool: ...

    def converge_cancelled(self, ownership: ExecutionOwnership) -> QueryRun | None: ...

    def converge_expired_cancellations(self) -> int: ...

    def get(self, run_id: str) -> QueryRun | None: ...

    def publish_success(self, ownership: ExecutionOwnership, result: QueryResult) -> QueryRun: ...

    def publish_failure(
        self,
        ownership: ExecutionOwnership,
        code: str,
        summary: str,
    ) -> QueryRun: ...


class Executor(Protocol):
    def execute(
        self,
        raw_sql: str,
        statement_timeout_ms: int,
        max_rows: int,
        cancellation: ExecutionCancellation,
    ) -> QueryResult: ...

    def cancel(self) -> bool: ...


class WorkerStopping(BaseException):
    pass


class QueryWorker:
    def __init__(
        self,
        repository: WorkerRepository,
        executor: Executor,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
        heartbeat_ms: int,
        max_execution_attempts: int = 3,
        retention: ResultRetention | None = None,
        cleanup_interval_ms: int = 60_000,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._worker_id = worker_id
        self._max_concurrency = max_concurrency
        self._lease_ms = lease_ms
        self._heartbeat_seconds = heartbeat_ms / 1_000
        self._max_execution_attempts = max_execution_attempts
        if cleanup_interval_ms <= 0:
            raise ValueError("cleanup_interval_ms must be positive")
        self._retention = retention
        self._cleanup_interval_seconds = cleanup_interval_ms / 1_000
        self._monotonic = monotonic_clock
        self._next_cleanup_at = 0.0

    def process_one(self) -> bool:
        self._clean_expired_results()
        self._repository.converge_expired_cancellations()
        ownership: ExecutionOwnership | None = None
        try:
            ownership = self._repository.claim_next(
                self._worker_id,
                self._max_concurrency,
                self._lease_ms,
                self._max_execution_attempts,
            )
            if ownership is None:
                return False
            return self._process_owned(ownership)
        except BaseException:
            if ownership is not None:
                self._repository.release_ownership(ownership)
            raise

    def _clean_expired_results(self) -> None:
        if self._retention is None:
            return
        now = self._monotonic()
        if now < self._next_cleanup_at:
            return
        self._next_cleanup_at = now + self._cleanup_interval_seconds
        try:
            deleted = self._retention.delete_expired()
        except Exception:
            LOGGER.warning("worker %s could not clean expired result snapshots", self._worker_id)
            return
        if deleted:
            LOGGER.info(
                "worker %s removed %d expired result snapshots",
                self._worker_id,
                deleted,
            )

    def _process_owned(self, ownership: ExecutionOwnership) -> bool:
        stop_heartbeat = Event()
        ownership_lost = Event()
        cancellation = ExecutionCancellation()

        def maintain_ownership() -> None:
            while not stop_heartbeat.wait(self._heartbeat_seconds):
                try:
                    current = self._repository.get(ownership.query_run.id)
                except Exception:
                    current = None
                if current is not None and current.status == "cancelling":
                    cancellation.request()
                    try:
                        self._executor.cancel()
                    except Exception:
                        pass
                try:
                    renewed = self._repository.renew_lease(ownership, self._lease_ms)
                except Exception:
                    renewed = None
                if renewed is None:
                    ownership_lost.set()
                    cancellation.request()
                    try:
                        self._executor.cancel()
                    except Exception:
                        pass
                    return

        heartbeat = Thread(target=maintain_ownership, name="worker-heartbeat", daemon=True)
        heartbeat.start()
        result: QueryResult | None = None
        failure: ExecutionFailure | None = None
        try:
            result = self._executor.execute(
                ownership.query_run.raw_sql,
                ownership.query_run.statement_timeout_ms,
                ownership.query_run.max_rows,
                cancellation,
            )
        except ExecutionFailure as exc:
            failure = exc
        except Exception:
            failure = ExecutionFailure("internal_error", "The query could not be completed.")
        finally:
            stop_heartbeat.set()
            heartbeat.join()

        if ownership_lost.is_set():
            return True
        try:
            if failure is not None:
                if (
                    failure.code == "analytics_unavailable"
                    and ownership.generation < self._max_execution_attempts
                ):
                    self._repository.release_for_next_attempt(ownership)
                else:
                    self._repository.publish_failure(
                        ownership,
                        failure.code,
                        failure.message,
                    )
            elif result is not None:
                self._repository.publish_success(ownership, result)
        except StateConflict:
            pass
        if not ownership_lost.is_set():
            try:
                self._repository.converge_cancelled(ownership)
            except StateConflict:
                pass
        return True


def main() -> None:
    settings = WorkerSettings.from_env()
    stopping = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()
        raise WorkerStopping

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker = QueryWorker(
        QueryRunRepository(settings.platform_database_url),
        PostgresQueryExecutor(settings.analytics_database_url, settings.max_concurrency),
        worker_id=f"{socket.gethostname()}-{uuid4()}",
        max_concurrency=settings.max_concurrency,
        lease_ms=settings.lease_ms,
        heartbeat_ms=settings.heartbeat_ms,
        max_execution_attempts=settings.max_execution_attempts,
        retention=ResultRetention(settings.platform_database_url),
        cleanup_interval_ms=settings.cleanup_interval_ms,
    )
    poll_seconds = settings.poll_ms / 1_000
    try:
        while not stopping.is_set():
            if not worker.process_one():
                stopping.wait(poll_seconds)
    except WorkerStopping:
        pass


if __name__ == "__main__":
    main()
