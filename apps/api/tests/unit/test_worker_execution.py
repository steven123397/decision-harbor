from datetime import datetime, timezone

import pytest

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure
from decisionharbor.worker.execution import QueryRunProcessor


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


def claimed_run(max_rows: int = 500) -> QueryRun:
    return QueryRun(
        id="3f0f1f6e-2f0b-4a1e-9a2a-3e9d1a2b4c5d",
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
        execution_attempt_count=1,
        attempt_number=1,
        attempt_worker_id="worker-test",
        attempt_generation=1,
        lease_expires_at=NOW,
        heartbeat_at=NOW,
    )


class FakeQueue:
    def __init__(self, run: QueryRun | None = None, *, publish_applied: bool = True) -> None:
        self.run = run
        self.publish_applied = publish_applied
        self.success: tuple[QueryRun, QueryResult] | None = None
        self.failure: tuple[QueryRun, str, str] | None = None

    def claim(self) -> QueryRun | None:
        return self.run

    def publish_success(self, run: QueryRun, result: QueryResult) -> bool:
        self.success = (run, result)
        return self.publish_applied

    def publish_failure(self, run: QueryRun, error_code: str, error_summary: str) -> bool:
        self.failure = (run, error_code, error_summary)
        return self.publish_applied


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


def test_claimed_run_publishes_its_result_snapshot() -> None:
    run = claimed_run()
    queue = FakeQueue(run)
    executor = FakeExecutor(RESULT)

    assert QueryRunProcessor(queue, executor).process_next() is True

    assert queue.success == (run, RESULT)
    assert queue.failure is None
    assert executor.calls == [(run.raw_sql, 5_000, 500)]


def test_claimed_run_is_executed_with_its_own_bounds() -> None:
    run = claimed_run(max_rows=2)
    executor = FakeExecutor(RESULT)

    QueryRunProcessor(FakeQueue(run), executor).process_next()

    assert executor.calls == [(run.raw_sql, 5_000, 2)]


@pytest.mark.parametrize(("code", "summary"), sorted(STABLE_FAILURES.items()))
def test_execution_failures_publish_a_stable_failed_run(code: str, summary: str) -> None:
    run = claimed_run()
    queue = FakeQueue(run)
    executor = FakeExecutor(failure=ExecutionFailure(code, summary))

    assert QueryRunProcessor(queue, executor).process_next() is True

    assert queue.success is None
    assert queue.failure == (run, code, summary)


def test_an_empty_queue_leaves_the_analytics_database_untouched() -> None:
    executor = FakeExecutor(RESULT)

    assert QueryRunProcessor(FakeQueue(), executor).process_next() is False

    assert executor.calls == []


def test_a_discarded_publish_does_not_retry_or_raise() -> None:
    queue = FakeQueue(claimed_run(), publish_applied=False)

    assert QueryRunProcessor(queue, FakeExecutor(RESULT)).process_next() is True

    assert queue.success is not None
