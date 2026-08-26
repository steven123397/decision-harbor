from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import sleep

import pytest

from decisionharbor.domain import ExecutionOwnership, QueryColumn, QueryResult, QueryRun
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

    def claim_next(
        self,
        worker_id: str,
        max_concurrency: int,
        lease_ms: int,
    ) -> ExecutionOwnership | None:
        if self.run.status != "queued":
            return None
        assert worker_id == "worker-a"
        assert max_concurrency == 4
        assert lease_ms == 100
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


class FakeExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        assert raw_sql == "SELECT count(*) FROM customers"
        assert statement_timeout_ms == 3_000
        assert max_rows == 25
        return QueryResult(
            columns=(QueryColumn(name="count", type="bigint"),),
            rows=(("100",),),
            truncated=False,
        )


class UnexpectedFailureExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        raise RuntimeError("postgresql://secret@database/raw-internal-detail")


class SlowExecutor(FakeExecutor):
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        sleep(0.03)
        return super().execute(raw_sql, statement_timeout_ms, max_rows)


class StoppingExecutor:
    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        raise WorkerStopping


def worker(repository: FakeRepository, executor) -> QueryWorker:
    return QueryWorker(
        repository,
        executor,
        worker_id="worker-a",
        max_concurrency=4,
        lease_ms=100,
        heartbeat_ms=5,
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


def test_worker_renews_ownership_while_the_query_is_running() -> None:
    repository = FakeRepository()

    assert worker(repository, SlowExecutor()).process_one() is True

    assert repository.renewals >= 1


def test_worker_releases_ownership_when_normal_stopping_interrupts_execution() -> None:
    repository = FakeRepository()

    with pytest.raises(WorkerStopping):
        worker(repository, StoppingExecutor()).process_one()

    assert repository.released is True
