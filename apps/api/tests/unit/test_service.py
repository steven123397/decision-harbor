from dataclasses import replace

import pytest

from decisionharbor.domain import QueryRun
from decisionharbor.policy import PolicyDecision
from decisionharbor.service import QueryRunService, ServiceFailure


class FakePolicy:
    def __init__(self, decision: PolicyDecision, error: Exception | None = None) -> None:
        self.decision = decision
        self.error = error

    def evaluate(self, raw_sql: str) -> PolicyDecision:
        if self.error:
            raise self.error
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


def build_service(
    repository: FakeRepository,
    decision: PolicyDecision,
    error: Exception | None = None,
) -> QueryRunService:
    return QueryRunService(
        repository=repository,
        policy=FakePolicy(decision, error),
        policy_version="policy-v1",
        statement_timeout_ms=5_000,
        max_rows=500,
    )


def test_audit_must_exist_before_policy_evaluation() -> None:
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


def test_policy_rejection_is_audited_without_queuing() -> None:
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


def test_policy_failure_is_audited_without_queuing() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ()),
        error=RuntimeError("parser detail must not escape"),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "policy_internal_error"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "failed"
    assert events == ["create", "transition:failed"]
    assert "parser" not in caught.value.message


def test_allowed_query_is_queued_without_execution() -> None:
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
    assert run.started_at is None
    assert events == ["create", "transition:queued"]
