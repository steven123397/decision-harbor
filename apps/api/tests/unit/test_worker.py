from dataclasses import replace
from datetime import datetime, timezone

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.worker import QueryWorker


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

    def claim_next(self) -> QueryRun | None:
        if self.run.status != "queued":
            return None
        self.run = replace(self.run, status="running", started_at=datetime.now(timezone.utc))
        return self.run

    def publish_success(self, run_id: str, result: QueryResult) -> QueryRun:
        assert run_id == self.run.id
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

    def publish_failure(self, run_id: str, code: str, summary: str) -> QueryRun:
        assert run_id == self.run.id
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


def test_worker_claims_a_queued_run_and_publishes_its_result() -> None:
    repository = FakeRepository()
    worker = QueryWorker(repository, FakeExecutor())

    assert worker.process_one() is True

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
    worker = QueryWorker(repository, UnexpectedFailureExecutor())

    assert worker.process_one() is True

    run = repository.get("75e24c21-416c-4bd8-a37d-68667f4ec753")
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "internal_error"
    assert run.error_summary == "The query could not be completed."
    assert "secret" not in run.error_summary
