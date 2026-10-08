from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import logging

from decisionharbor.domain import QueryRun
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.worker.leases import LeaseHeartbeat
from decisionharbor.worker.queue import QueryRunQueue


LOGGER = logging.getLogger(__name__)


class QueryRunProcessor:
    """Runs claimed query runs and publishes their terminal state.

    Execution failures become stable error codes before they reach the audit
    record, so neither the response nor the persisted summary can carry a raw
    database message.

    A replica runs as many executions as the configured concurrency allows and
    no more, but that bound is only this process's own share: the capacity no
    replica may cross is enforced by the database the moment a run is claimed.
    """

    def __init__(
        self,
        queue: QueryRunQueue,
        executor: PostgresQueryExecutor,
        leases: LeaseHeartbeat,
        capacity: int,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._queue = queue
        self._executor = executor
        self._leases = leases
        self._capacity = capacity
        self._pool = (
            None
            if spawn
            else ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="query-run")
        )
        self._spawn = spawn if spawn else self._pool.submit

    def process_available(self) -> int:
        """Claim and start every run this replica may own, reporting how many.

        Claiming stops when the database refuses one: either no run is waiting,
        or the shared capacity is used up. A run stays rented from the moment it
        is claimed until its outcome is published, so the heartbeat holds its
        lease for as long as this replica owns it.
        """
        started = 0
        while self._leases.owned_count < self._capacity:
            claimed = self._queue.claim()
            if claimed is None:
                break
            self._leases.track(claimed)
            self._spawn(partial(self._process, claimed))
            started += 1
        return started

    def close(self) -> None:
        """Wait for the executions already started to publish their outcome."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)

    def _process(self, run: QueryRun) -> None:
        try:
            self._execute(run)
        finally:
            self._leases.release(run)

    def _execute(self, run: QueryRun) -> None:
        try:
            result = self._executor.execute(
                run.raw_sql,
                run.statement_timeout_ms,
                run.max_rows,
            )
        except ExecutionFailure as failure:
            self._publish_failure(run, failure.code, failure.message)
            return
        if not self._queue.publish_success(run, result):
            LOGGER.warning("query run %s discarded its result snapshot", run.id)

    def _publish_failure(self, run: QueryRun, error_code: str, error_summary: str) -> None:
        if not self._queue.publish_failure(run, error_code, error_summary):
            LOGGER.warning("query run %s discarded its execution failure", run.id)
