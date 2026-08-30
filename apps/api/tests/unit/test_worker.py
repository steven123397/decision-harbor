from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from time import sleep

import pytest

from decisionharbor.domain import ExecutionOwnership, QueryColumn, QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure
from decisionharbor.worker import QueryWorker, WorkerStopping


class FakeRepository:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.run = QueryRun(
            id="75e24c21-416c-4bd8-a37d-68667f4ec753",
            raw_sql="SELECT count(*) FROM customers",
            status="queued",
            policy_decision="allowed",
            policy_version="policy-v1",
            referenced_objects=("analytics.customers",),
            statement_timeout_ms=3_000,
            max_rows=25,
            returned_row_count=None,
            result_truncated=None,
            error_code=None,
            error_summary=None,
            created_at=now,
            started_at=None,
            finished_at=None,
            duration_ms=None,
        )
        self.result: QueryResult | None = None
        self.renewals = 0
        self.released = False
        self.next_attempt_release_reason: str | None = None

    def claim_next(
        self,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
        max_execution_attempts: int,
    ) -> ExecutionOwnership | None:
        if self.run.status != "queued":
            return None
        assert worker_id == "worker-a"
        assert max_concurrency == 4
        assert lease_ms == 100
        assert max_execution_attempts > 0
        heartbeat_at = datetime.now(timezone.utc)
        self.run = replace(self.run, status="running", started_at=datetime.now(timezone.utc))
        return ExecutionOwnership(
            query_run=self.run,
            worker_id=worker_id,
            generation=1,
            heartbeat_at=heartbeat_at,
            lease_expires_at=heartbeat_at + timedelta(milliseconds=lease_ms),
        )

    def renew_lease(
        self,
        ownership: ExecutionOwnership,
        lease_ms: int,
    ) -> ExecutionOwnership | None:
        self.renewals += 1
        heartbeat_at = datetime.now(timezone.utc)
        return replace(
            ownership,
            heartbeat_at=heartbeat_at,
            lease_expires_at=heartbeat_at + timedelta(milliseconds=lease_ms),
        )

    def release_ownership(self, ownership: ExecutionOwnership) -> bool:
        assert ownership.query_run.id == self.run.id
        self.released = True
        return True

    def release_for_next_attempt(self, ownership: ExecutionOwnership) -> bool:
        assert ownership.query_run.id == self.run.id
        self.next_attempt_release_reason = "analytics_unavailable"
        self.run = replace(self.run, status="running")
        return True

    def publish_success(self, ownership: ExecutionOwnership, result: QueryResult) -> QueryRun:
        assert ownership.query_run.id == self.run.id
        self.result = result
        self.run = replace(
            self.run,
            status="succeeded",
            returned_row_count=len(result.rows),
            result_truncated=result.truncated,
            finished_at=datetime.now(timezone.utc),
            duration_ms=7,
        )
        return self.run

    def publish_failure(
        self,
        ownership: ExecutionOwnership,
        code: str,
        summary: str,
    ) -> QueryRun:
        assert ownership.query_run.id == self.run.id
        self.run = replace(
            self.run,
            status="failed",
            error_code=code,
            error_summary=summary,
            finished_at=datetime.now(timezone.utc),
            duration_ms=7,
        )
        return self.run

    def get(self, run_id: str) -> QueryRun | None:
        return self.run if run_id == self.run.id else None

    def get_result(self, run_id: str) -> QueryResult | None:
        return self.result if run_id == self.run.id else None


class LosingRepository(FakeRepository):
    def renew_lease(
        self,
        ownership: ExecutionOwnership,
        lease_ms: int,
    ) -> ExecutionOwnership | None:
        self.renewals += 1
        return None


class RenewalFailureRepository(FakeRepository):
    def renew_lease(
        self,
        ownership: ExecutionOwnership,
        lease_ms: int,
    ) -> ExecutionOwnership | None:
        self.renewals += 1
        raise RuntimeError("platform unavailable")


class FakeExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        assert raw_sql == "SELECT count(*) FROM customers"
        assert statement_timeout_ms == 3_000
        assert max_rows == 25
        return QueryResult(
            columns=(QueryColumn(name="count", type="bigint"),),
            rows=(("100",),),
            truncated=False,
        )


class UnexpectedFailureExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        raise RuntimeError("postgresql://secret@database/raw-internal-detail")


class AnalyticsUnavailableExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        raise ExecutionFailure("analytics_unavailable", "The analytics database is unavailable.")


class KnownFailureExecutor:
    def __init__(self, code: str) -> None:
        self._code = code

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        raise ExecutionFailure(self._code, f"Stable {self._code} summary.")


class SlowExecutor(FakeExecutor):
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        sleep(0.03)
        return super().execute(raw_sql, statement_timeout_ms, max_rows, cancellation)


class CancelableSlowExecutor(FakeExecutor):
    def __init__(self) -> None:
        self.cancel_requested = Event()

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        self.cancel_requested.wait(0.05)
        return super().execute(raw_sql, statement_timeout_ms, max_rows, cancellation)

    def cancel(self) -> bool:
        self.cancel_requested.set()
        return True


class RegistrationDelayedExecutor(FakeExecutor):
    def __init__(self) -> None:
        self.cancel_requested_before_sql = False

    def execute(
        self,
        raw_sql: str,
        statement_timeout_ms: int,
        max_rows: int,
        cancellation,
    ) -> QueryResult:
        sleep(0.03)
        self.cancel_requested_before_sql = cancellation.is_requested()
        if self.cancel_requested_before_sql:
            raise ExecutionFailure("internal_error", "Execution was cancelled before SQL started.")
        return super().execute(raw_sql, statement_timeout_ms, max_rows)

    def cancel(self) -> bool:
        return False


class StoppingExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int, cancellation=None) -> QueryResult:
        raise WorkerStopping


def worker(
    repository: FakeRepository,
    executor,
    max_execution_attempts: int = 3,
) -> QueryWorker:
    return QueryWorker(
        repository,
        executor,
        worker_id="worker-a",
        max_concurrency=4,
        lease_ms=100,
        heartbeat_ms=5,
        max_execution_attempts=max_execution_attempts,
    )


def test_worker_claims_a_queued_run_and_publishes_its_result() -> None:
    repository = FakeRepository()
    query_worker = worker(repository, FakeExecutor())

    assert query_worker.process_one() is True

    run = repository.get("75e24c21-416c-4bd8-a37d-68667f4ec753")
    assert run is not None
    assert run.status == "succeeded"
    assert run.returned_row_count == 1
    assert repository.get_result(run.id) == QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=(("100",),),
        truncated=False,
    )


def test_worker_publishes_a_safe_failure_for_an_unknown_exception() -> None:
    repository = FakeRepository()
    query_worker = worker(repository, UnexpectedFailureExecutor())

    assert query_worker.process_one() is True

    run = repository.get("75e24c21-416c-4bd8-a37d-68667f4ec753")
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "internal_error"
    assert run.error_summary == "The query could not be completed."
    assert "secret" not in run.error_summary


def test_worker_releases_analytics_unavailable_for_another_execution_attempt() -> None:
    repository = FakeRepository()
    query_worker = worker(repository, AnalyticsUnavailableExecutor())

    assert query_worker.process_one() is True

    run = repository.get("75e24c21-416c-4bd8-a37d-68667f4ec753")
    assert run is not None
    assert run.status == "running"
    assert run.error_code is None
    assert repository.next_attempt_release_reason == "analytics_unavailable"


@pytest.mark.parametrize(
    "code",
    [
        "query_timeout",
        "query_semantic_error",
        "result_too_large",
        "unsupported_result_type",
        "internal_error",
    ],
)
def test_worker_does_not_automatically_retry_permanent_failures(code: str) -> None:
    repository = FakeRepository()

    assert worker(repository, KnownFailureExecutor(code)).process_one() is True

    assert repository.run.status == "failed"
    assert repository.run.error_code == code
    assert repository.next_attempt_release_reason is None


def test_worker_fails_analytics_unavailable_at_the_attempt_limit() -> None:
    repository = FakeRepository()

    assert worker(
        repository,
        AnalyticsUnavailableExecutor(),
        max_execution_attempts=1,
    ).process_one() is True

    assert repository.run.status == "failed"
    assert repository.run.error_code == "analytics_unavailable"
    assert repository.next_attempt_release_reason is None


def test_worker_renews_ownership_while_the_query_is_running() -> None:
    repository = FakeRepository()

    assert worker(repository, SlowExecutor()).process_one() is True

    assert repository.renewals >= 1


def test_worker_requests_cancellation_when_execution_ownership_is_lost() -> None:
    repository = LosingRepository()
    executor = CancelableSlowExecutor()

    assert worker(repository, executor).process_one() is True

    assert repository.renewals == 1
    assert executor.cancel_requested.is_set()
    assert repository.run.status == "running"
    assert repository.result is None


def test_worker_treats_a_lease_renewal_error_as_lost_ownership() -> None:
    repository = RenewalFailureRepository()
    executor = CancelableSlowExecutor()

    assert worker(repository, executor).process_one() is True

    assert repository.renewals == 1
    assert executor.cancel_requested.is_set()
    assert repository.run.status == "running"
    assert repository.result is None


def test_worker_carries_lost_ownership_into_executor_registration() -> None:
    repository = RenewalFailureRepository()
    executor = RegistrationDelayedExecutor()

    assert worker(repository, executor).process_one() is True

    assert repository.renewals == 1
    assert executor.cancel_requested_before_sql is True
    assert repository.run.status == "running"
    assert repository.result is None


def test_worker_releases_ownership_when_normal_stopping_interrupts_execution() -> None:
    repository = FakeRepository()

    with pytest.raises(WorkerStopping):
        worker(repository, StoppingExecutor()).process_one()

    assert repository.released is True
