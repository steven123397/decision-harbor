from datetime import datetime, timezone

import pytest

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure
from decisionharbor.worker.execution import QueryRunProcessor
from decisionharbor.worker.leases import LeaseHeartbeat


NOW = datetime.now(timezone.utc)

RESULT = QueryResult(
    columns=(QueryColumn(name="id", type="bigint"),),
    rows=(("1",), ("2",)),
    truncated=False,
)

STABLE_FAILURES = {
    "query_timeout": "The query exceeded its time limit.",
    "query_semantic_error": "The query is not valid for this dataset.",
    "unsupported_result_type": "The query returned a result type that is not supported.",
    "analytics_unavailable": "The analytics database is unavailable.",
    "internal_error": "The query could not be completed.",
}


def claimed_run(
    max_rows: int = 500,
    *,
    identifier: str = "3f0f1f6e-2f0b-4a1e-9a2a-3e9d1a2b4c5d",
    generation: int = 1,
) -> QueryRun:
    return QueryRun(
        id=identifier,
        raw_sql="SELECT id FROM customers ORDER BY id",
        status="running",
        policy_decision="allowed",
        policy_version="policy-v1",
        referenced_objects=("analytics.customers",),
        statement_timeout_ms=5_000,
        max_rows=max_rows,
        returned_row_count=None,
        result_truncated=None,
        error_code=None,
        error_summary=None,
        created_at=NOW,
        started_at=NOW,
        finished_at=None,
        duration_ms=None,
        execution_attempt_count=generation,
        attempt_number=generation,
        attempt_worker_id="worker-test",
        attempt_generation=generation,
        lease_expires_at=NOW,
        heartbeat_at=NOW,
    )


class FakeQueue:
    """A queue that hands out the runs it was given and then refuses to claim.

    A refusal is how the real queue reports both an empty queue and a shared
    capacity that is used up, so the processor cannot tell the two apart and
    must stop claiming either way.
    """

    def __init__(
        self,
        runs: tuple[QueryRun, ...] = (),
        *,
        publish_applied: bool = True,
    ) -> None:
        self._runs = list(runs)
        self.publish_applied = publish_applied
        self.claims = 0
        self.success: tuple[QueryRun, QueryResult] | None = None
        self.failure: tuple[QueryRun, str, str] | None = None
        self.renewals: list[QueryRun] = []

    def claim(self) -> QueryRun | None:
        self.claims += 1
        return self._runs.pop(0) if self._runs else None

    def publish_success(self, run: QueryRun, result: QueryResult) -> bool:
        self.success = (run, result)
        return self.publish_applied

    def publish_failure(self, run: QueryRun, error_code: str, error_summary: str) -> bool:
        self.failure = (run, error_code, error_summary)
        return self.publish_applied

    def renew_lease(self, run: QueryRun) -> bool:
        self.renewals.append(run)
        return True


class FakeExecutor:
    def __init__(self, result: QueryResult | None = None, failure: ExecutionFailure | None = None) -> None:
        self.result = result
        self.failure = failure
        self.calls: list[tuple[str, int, int]] = []

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        self.calls.append((raw_sql, statement_timeout_ms, max_rows))
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


def synchronous(queue: FakeQueue, executor: FakeExecutor, capacity: int = 4) -> QueryRunProcessor:
    """A processor that runs every claimed run before it claims the next one."""
    return QueryRunProcessor(queue, executor, LeaseHeartbeat(queue, 1_000), capacity, spawn=lambda task: task())


def test_claimed_run_publishes_its_result_snapshot() -> None:
    run = claimed_run()
    queue = FakeQueue((run,))
    executor = FakeExecutor(RESULT)

    assert synchronous(queue, executor).process_available() == 1

    assert queue.success == (run, RESULT)
    assert queue.failure is None
    assert executor.calls == [(run.raw_sql, 5_000, 500)]


def test_claimed_run_is_executed_with_its_own_bounds() -> None:
    run = claimed_run(max_rows=2)
    executor = FakeExecutor(RESULT)

    synchronous(FakeQueue((run,)), executor).process_available()

    assert executor.calls == [(run.raw_sql, 5_000, 2)]


@pytest.mark.parametrize(("code", "summary"), sorted(STABLE_FAILURES.items()))
def test_execution_failures_publish_a_stable_failed_run(code: str, summary: str) -> None:
    run = claimed_run()
    queue = FakeQueue((run,))
    executor = FakeExecutor(failure=ExecutionFailure(code, summary))

    assert synchronous(queue, executor).process_available() == 1

    assert queue.success is None
    assert queue.failure == (run, code, summary)


def test_an_empty_queue_leaves_the_analytics_database_untouched() -> None:
    executor = FakeExecutor(RESULT)

    assert synchronous(FakeQueue(), executor).process_available() == 0

    assert executor.calls == []


def test_a_discarded_publish_does_not_retry_or_raise() -> None:
    queue = FakeQueue((claimed_run(),), publish_applied=False)

    assert synchronous(queue, FakeExecutor(RESULT)).process_available() == 1

    assert queue.success is not None


def test_a_replica_owns_no_more_runs_than_its_capacity() -> None:
    runs = tuple(
        claimed_run(identifier=f"3f0f1f6e-2f0b-4a1e-9a2a-3e9d1a2b4c5{generation}", generation=generation)
        for generation in (1, 2, 3, 4, 5)
    )
    queue = FakeQueue(runs)
    leases = LeaseHeartbeat(queue, 1_000)
    started: list[object] = []

    claimed = QueryRunProcessor(queue, FakeExecutor(RESULT), leases, 2, spawn=started.append).process_available()

    assert claimed == 2
    assert leases.owned_count == 2


def test_claiming_stops_at_the_first_claim_the_database_refuses() -> None:
    # The queue refuses for two reasons the processor cannot tell apart, an
    # empty queue and a shared capacity that is used up; both end the cycle.
    queue = FakeQueue((claimed_run(),))

    assert synchronous(queue, FakeExecutor(RESULT)).process_available() == 1

    assert queue.claims == 2


def test_a_published_run_stops_being_leased() -> None:
    queue = FakeQueue((claimed_run(),))
    leases = LeaseHeartbeat(queue, 1_000)
    processor = QueryRunProcessor(queue, FakeExecutor(RESULT), leases, 4, spawn=lambda task: task())

    processor.process_available()

    assert leases.owned_count == 0
