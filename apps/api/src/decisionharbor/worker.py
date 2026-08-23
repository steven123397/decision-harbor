from time import sleep
from typing import Protocol

from decisionharbor.config import WorkerSettings
from decisionharbor.domain import QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository


class WorkerRepository(Protocol):
    def claim_next(self) -> QueryRun | None: ...

    def publish_success(self, run_id: str, result: QueryResult) -> QueryRun: ...

    def publish_failure(self, run_id: str, code: str, summary: str) -> QueryRun: ...


class Executor(Protocol):
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult: ...


class QueryWorker:
    def __init__(self, repository: WorkerRepository, executor: Executor) -> None:
        self._repository = repository
        self._executor = executor

    def process_one(self) -> bool:
        run = self._repository.claim_next()
        if run is None:
            return False

        try:
            result = self._executor.execute(run.raw_sql, run.statement_timeout_ms, run.max_rows)
        except ExecutionFailure as exc:
            self._repository.publish_failure(run.id, exc.code, exc.message)
        except Exception:
            self._repository.publish_failure(run.id, "internal_error", "The query could not be completed.")
        else:
            self._repository.publish_success(run.id, result)
        return True


def main() -> None:
    settings = WorkerSettings.from_env()
    worker = QueryWorker(
        QueryRunRepository(settings.platform_database_url),
        PostgresQueryExecutor(settings.analytics_database_url, settings.max_concurrency),
    )
    poll_seconds = settings.poll_ms / 1_000
    while True:
        if not worker.process_one():
            sleep(poll_seconds)


if __name__ == "__main__":
    main()
