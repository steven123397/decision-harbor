from dataclasses import replace

import pytest

from decisionharbor.domain import QueryColumn, QueryResult, QueryRun, QueryRunCreation, StoredResult
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

    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        idempotency_key: str | None = None,
    ) -> QueryRunCreation:
        self.events.append("create")
        if self.fail_create:
            raise RuntimeError("database DSN and secret must not escape")
        self.run = replace(self.run, raw_sql=raw_sql)
        return QueryRunCreation(query_run=self.run, created=True)

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        self.events.append(f"transition:{changes['status']}")
        assert self.run.status == expected_status
        self.run = replace(self.run, **changes)
        return self.run

    def get(self, run_id: str) -> QueryRun | None:
        return self.run if run_id == self.run.id else None


class ResultRepository(FakeRepository):
    def __init__(self, stored: StoredResult | None) -> None:
        super().__init__([])
        self.stored = stored

    def get_result_state(self, run_id: str) -> StoredResult | None:
        return self.stored if self.stored is None or run_id == self.stored.run.id else None


def build_service(
    repository: FakeRepository,
    decision: PolicyDecision,
) -> QueryRunService:
    return QueryRunService(
        repository=repository,
        policy=FakePolicy(decision),
        policy_version="policy-v1",
        statement_timeout_ms=5_000,
        max_rows=500,
    )


def test_audit_must_exist_before_policy_or_execution() -> None:
    events: list[str] = []
    repository = FakeRepository(events, fail_create=True)
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "audit_unavailable"
    assert caught.value.query_run is None
    assert events == ["create"]
    assert "DSN" not in caught.value.message


def test_policy_rejection_is_audited_without_executing() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(
        repository,
        PolicyDecision(False, "sql_object_not_allowed", "Object is not allowed.", ()),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT * FROM secrets")

    assert caught.value.code == "sql_object_not_allowed"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "rejected"
    assert events == ["create", "transition:rejected"]


def test_allowed_submission_is_queued_without_executing() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    run = service.submit("SELECT 1")

    assert run.status == "queued"
    assert run.policy_decision == "allowed"
    assert run.referenced_objects == ("analytics.customers",)
    assert events == ["create", "transition:queued"]


def test_result_read_returns_the_retained_snapshot() -> None:
    snapshot = QueryResult(
        columns=(QueryColumn(name="answer", type="integer"),),
        rows=((1,),),
        truncated=False,
    )
    repository = ResultRepository(None)
    succeeded = replace(
        repository.run,
        status="succeeded",
        policy_decision="allowed",
        started_at=repository.run.created_at,
        finished_at=repository.run.created_at,
        returned_row_count=1,
        result_truncated=False,
        duration_ms=0,
    )
    repository.stored = StoredResult(
        run=succeeded,
        snapshot=snapshot,
        result_expired=False,
    )
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    assert service.read_result(succeeded.id) == snapshot


def test_result_read_reports_a_cleaned_succeeded_snapshot_as_expired() -> None:
    repository = ResultRepository(None)
    succeeded = replace(
        repository.run,
        status="succeeded",
        policy_decision="allowed",
        started_at=repository.run.created_at,
        finished_at=repository.run.created_at,
        returned_row_count=1,
        result_truncated=False,
        duration_ms=0,
    )
    repository.stored = StoredResult(
        run=succeeded,
        snapshot=None,
        result_expired=True,
    )
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.read_result(succeeded.id)

    assert caught.value.code == "result_expired"
    assert caught.value.query_run == succeeded
