from dataclasses import replace

import pytest

from decisionharbor.domain import QueryRun
from decisionharbor.policy import PolicyDecision
from decisionharbor.service import QueryRunService, ServiceFailure


class FakePolicy:
    def __init__(self, decision: PolicyDecision, *, fail: bool = False) -> None:
        self.decision = decision
        self.fail = fail

    def evaluate(self, raw_sql: str) -> PolicyDecision:
        if self.fail:
            raise RuntimeError("sqlglot internals must not escape")
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


def build_service(repository: FakeRepository, decision: PolicyDecision, *, policy_fails: bool = False) -> QueryRunService:
    return QueryRunService(
        repository=repository,
        policy=FakePolicy(decision, fail=policy_fails),
        policy_version="policy-v1",
        statement_timeout_ms=5_000,
        max_rows=500,
    )


ALLOWED = PolicyDecision(True, None, None, ("analytics.customers",))


def test_audit_must_exist_before_policy_or_queueing() -> None:
    events: list[str] = []
    repository = FakeRepository(events, fail_create=True)
    service = build_service(repository, ALLOWED)

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "audit_unavailable"
    assert caught.value.query_run is None
    assert events == ["create"]
    assert "DSN" not in caught.value.message


def test_allowed_submission_is_enqueued_with_policy_facts() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(repository, ALLOWED)

    run = service.submit("SELECT 1")

    assert run.status == "queued"
    assert run.policy_decision == "allowed"
    assert run.referenced_objects == ("analytics.customers",)
    assert run.error_code is None
    assert events == ["create", "transition:queued"]


def test_policy_rejection_is_audited_without_queueing() -> None:
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
    assert caught.value.query_run.policy_decision == "rejected"
    assert events == ["create", "transition:rejected"]


def test_policy_internal_error_closes_the_run_as_failed() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(repository, ALLOWED, policy_fails=True)

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "policy_internal_error"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "failed"
    assert caught.value.query_run.error_code == "policy_internal_error"
    assert "sqlglot" not in caught.value.message
    assert events == ["create", "transition:failed"]


def test_transition_failure_maps_to_audit_unavailable() -> None:
    repository = FakeRepository([])

    def broken_transition(run_id: str, expected_status: str, **changes: object) -> QueryRun:
        raise RuntimeError("connection reset with DSN")

    repository.transition = broken_transition  # type: ignore[method-assign]
    service = build_service(repository, ALLOWED)

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "audit_unavailable"
    assert "DSN" not in caught.value.message
