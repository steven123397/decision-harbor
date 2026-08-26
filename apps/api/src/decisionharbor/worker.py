import signal
import socket
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import ExecutionOwnership, QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository, StateConflict


class WorkerRepository(Protocol):
    def claim_next(
        self,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
    ) -> ExecutionOwnership | None: ...

    def renew_lease(
        self,
        ownership: ExecutionOwnership,
        lease_ms: int,
    ) -> ExecutionOwnership | None: ...

    def release_ownership(self, ownership: ExecutionOwnership) -> bool: ...

    def publish_success(self, ownership: ExecutionOwnership, result: QueryResult) -> QueryRun: ...

    def publish_failure(
        self,
        ownership: ExecutionOwnership,
        code: str,
        summary: str,
    ) -> QueryRun: ...


class Executor(Protocol):
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult: ...


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
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._worker_id = worker_id
        self._max_concurrency = max_concurrency
        self._lease_ms = lease_ms
        self._heartbeat_seconds = heartbeat_ms / 1_000

    def process_one(self) -> bool:
        ownership: ExecutionOwnership | None = None
        try:
            ownership = self._repository.claim_next(
                self._worker_id,
                self._max_concurrency,
                self._lease_ms,
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

        def maintain_ownership() -> None:
            while not stop_heartbeat.wait(self._heartbeat_seconds):
                if self._repository.renew_lease(ownership, self._lease_ms) is None:
                    ownership_lost.set()
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
