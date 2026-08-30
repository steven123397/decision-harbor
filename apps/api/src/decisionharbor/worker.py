import signal
import socket
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import ExecutionOwnership, QueryResult, QueryRun
from decisionharbor.executor import ExecutionCancellation, ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository, StateConflict


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
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._worker_id = worker_id
        self._max_concurrency = max_concurrency
        self._lease_ms = lease_ms
        self._heartbeat_seconds = heartbeat_ms / 1_000
        self._max_execution_attempts = max_execution_attempts

    def process_one(self) -> bool:
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

    def _process_owned(self, ownership: ExecutionOwnership) -> bool:
        stop_heartbeat = Event()
        ownership_lost = Event()
        cancellation = ExecutionCancellation()

        def maintain_ownership() -> None:
            while not stop_heartbeat.wait(self._heartbeat_seconds):
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
