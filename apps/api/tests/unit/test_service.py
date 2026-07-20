from dataclasses import replace

import pytest

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.executor import ExecutionFailure
from decisionharbor.policy import PolicyDecision
from decisionharbor.service import QueryRunService, ServiceFailure


class FakePolicy:
    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision

    def evaluate(self, raw_sql: str) -> PolicyDecision:
        return self.decision


class FakeRepository:
    def __init__(self, events: list[str], *, fail_create: bool = False) -> None:
        self.events = events
        self.fail_create = fail_create
        self.run = QueryRun.received(
            raw_sql="SELECT 1",
            policy_version="policy-v1",
            statement_timeout_ms=5_000,
            max_rows=500,
        )

    def create(self, raw_sql: str, policy_version: str, statement_timeout_ms: int, max_rows: int) -> QueryRun:
        self.events.append("create")
        if self.fail_create:
            raise RuntimeError("database DSN and secret must not escape")
        self.run = replace(self.run, raw_sql=raw_sql)
        return self.run

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        self.events.append(f"transition:{changes['status']}")
        assert self.run.status == expected_status
        self.run = replace(self.run, **changes)
        return self.run


class FakeExecutor:
    def __init__(self, events: list[str], failure: ExecutionFailure | None = None) -> None:
        self.events = events
        self.failure = failure

    def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
        self.events.append("execute")
        if self.failure:
            raise self.failure
        return QueryResult(
            columns=(QueryColumn(name="answer", type="integer"),),
            rows=((1,),),
            truncated=False,
        )


def build_service(
    repository: FakeRepository,
    executor: FakeExecutor,
    decision: PolicyDecision,
) -> QueryRunService:
    return QueryRunService(
        repository=repository,
        policy=FakePolicy(decision),
        executor=executor,
        policy_version="policy-v1",
        statement_timeout_ms=5_000,
        max_rows=500,
        max_concurrency=1,
        capacity_wait_ms=0,
    )


def test_audit_must_exist_before_policy_or_execution() -> None:
    events: list[str] = []
    repository = FakeRepository(events, fail_create=True)
    executor = FakeExecutor(events)
    service = build_service(
        repository,
        executor,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.run("SELECT 1")

    assert caught.value.code == "audit_unavailable"
    assert caught.value.query_run is None
    assert events == ["create"]
    assert "DSN" not in caught.value.message


def test_policy_rejection_is_audited_without_executing() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    executor = FakeExecutor(events)
    service = build_service(
        repository,
        executor,
        PolicyDecision(False, "sql_object_not_allowed", "Object is not allowed.", ()),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.run("SELECT * FROM secrets")

    assert caught.value.code == "sql_object_not_allowed"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "rejected"
    assert events == ["create", "transition:rejected"]


def test_success_is_returned_only_after_terminal_audit() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    executor = FakeExecutor(events)
    service = build_service(
        repository,
        executor,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    outcome = service.run("SELECT 1")

    assert outcome.query_run.status == "succeeded"
    assert outcome.query_run.returned_row_count == 1
    assert outcome.result.rows == ((1,),)
    assert events == ["create", "transition:running", "execute", "transition:succeeded"]


def test_execution_failure_is_safely_mapped_and_audited() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    executor = FakeExecutor(
        events,
        ExecutionFailure("query_semantic_error", "The query is not valid for this dataset."),
    )
    service = build_service(
        repository,
        executor,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.run("SELECT missing_column")

    assert caught.value.code == "query_semantic_error"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "failed"
    assert caught.value.query_run.error_summary == "The query is not valid for this dataset."
    assert events == ["create", "transition:running", "execute", "transition:failed"]


def test_capacity_exhaustion_fails_before_running_or_execution() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    executor = FakeExecutor(events)
    service = build_service(
        repository,
        executor,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )
    assert service._capacity.acquire(blocking=False) is True

    with pytest.raises(ServiceFailure) as caught:
        service.run("SELECT 1")

    service._capacity.release()
    assert caught.value.code == "query_capacity_exceeded"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "failed"
    assert caught.value.query_run.policy_decision == "allowed"
    assert caught.value.query_run.referenced_objects == ("analytics.customers",)
    assert events == ["create", "transition:failed"]
