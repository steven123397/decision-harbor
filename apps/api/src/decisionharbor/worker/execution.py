import logging

from decisionharbor.domain import QueryRun
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.worker.queue import QueryRunQueue


LOGGER = logging.getLogger(__name__)


class QueryRunProcessor:
    """Runs one claimed query run and publishes its terminal state.

    Execution failures become stable error codes before they reach the audit
    record, so neither the response nor the persisted summary can carry a raw
    database message.
    """

    def __init__(self, queue: QueryRunQueue, executor: PostgresQueryExecutor) -> None:
        self._queue = queue
        self._executor = executor

    def process_next(self) -> bool:
        """Claim the oldest queued run and publish its terminal state.

        Reports whether a run was claimed; a claimed run whose publish the
        database refused stays in the state its newer owner gave it.
        """
        claimed = self._queue.claim()
        if claimed is None:
            return False
        try:
            result = self._executor.execute(
                claimed.raw_sql,
                claimed.statement_timeout_ms,
                claimed.max_rows,
            )
        except ExecutionFailure as failure:
            self._publish_failure(claimed, failure.code, failure.message)
            return True
        if not self._queue.publish_success(claimed, result):
            LOGGER.warning("query run %s discarded its result snapshot", claimed.id)
        return True

    def _publish_failure(self, run: QueryRun, error_code: str, error_summary: str) -> None:
        if not self._queue.publish_failure(run, error_code, error_summary):
            LOGGER.warning("query run %s discarded its execution failure", run.id)
