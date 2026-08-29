from dataclasses import replace
from datetime import datetime, timezone

import pytest

from decisionharbor.domain import (
    SUBMIT_IDEMPOTENCY_SCOPE,
    IdempotencyClaim,
    QueryRun,
    SubmitReservation,
    submit_request_fingerprint,
)
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
    def __init__(
        self,
        events: list[str],
        *,
        fail_reserve: bool = False,
        replay: SubmitReservation | None = None,
    ) -> None:
        self.events = events
        self.fail_reserve = fail_reserve
        self.replay = replay
        self.runs: dict[str, QueryRun] = {}
        self.claims: list[IdempotencyClaim | None] = []

    def reserve(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        *,
        idempotency: IdempotencyClaim | None,
    ) -> SubmitReservation:
        self.events.append("reserve")
        self.claims.append(idempotency)
        if self.fail_reserve:
            raise RuntimeError("database DSN and secret must not escape")
        if self.replay is not None:
            return self.replay
        run = replace(
            QueryRun.received(raw_sql, policy_version, statement_timeout_ms, max_rows),
            id=f"run-{len(self.runs) + 1}",
        )
        self.runs[run.id] = run
        return SubmitReservation.created(run)

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        self.events.append(f"transition:{changes['status']}")
        run = self.runs[run_id]
        assert run.status == expected_status
        self.runs[run_id] = replace(run, **changes)
        return self.runs[run_id]


def replay_reservation(
    status: str = "queued",
    code: str | None = None,
    fingerprint_matched: bool = True,
) -> SubmitReservation:
    finished_at = datetime.now(timezone.utc)
    run = QueryRun(
        id="run-original",
        raw_sql="SELECT 1",
        status=status,
        policy_decision="rejected" if status == "rejected" else "allowed",
        policy_version="policy-v1",
        referenced_objects=(),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=None,
        result_truncated=None,
        error_code=code,
        error_summary="Object is not allowed." if code else None,
        created_at=finished_at,
        started_at=None,
        finished_at=finished_at if status == "rejected" else None,
        duration_ms=1 if status == "rejected" else None,
    )
    return SubmitReservation.replayed(run, fingerprint_matched)


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
    repository = FakeRepository(events, fail_reserve=True)
    service = build_service(
        repository,
        PolicyDecision(True, None, None, ("analytics.customers",)),
    )

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1")

    assert caught.value.code == "audit_unavailable"
    assert caught.value.query_run is None
    assert events == ["reserve"]
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
    assert events == ["reserve", "transition:rejected"]


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
    assert events == ["reserve", "transition:failed"]
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
    assert events == ["reserve", "transition:queued"]


def test_submit_reserves_the_instance_wide_submit_scope() -> None:
    repository = FakeRepository([])
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    service.submit("SELECT 1", "submit-key")

    assert repository.claims == [
        IdempotencyClaim(
            scope=SUBMIT_IDEMPOTENCY_SCOPE,
            key="submit-key",
            request_fingerprint=submit_request_fingerprint("SELECT 1"),
        )
    ]


def test_submit_without_a_key_reserves_no_idempotency_claim() -> None:
    repository = FakeRepository([])
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    service.submit("SELECT 1")

    assert repository.claims == [None]


def test_replayed_submit_returns_the_original_run_without_a_new_transition() -> None:
    events: list[str] = []
    repository = FakeRepository(events, replay=replay_reservation())
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    run = service.submit("SELECT 1", "submit-key")

    assert run.id == "run-original"
    assert run.status == "queued"
    assert events == ["reserve"]


def test_replayed_submit_replays_the_recorded_policy_rejection() -> None:
    events: list[str] = []
    repository = FakeRepository(
        events,
        replay=replay_reservation("rejected", "sql_object_not_allowed"),
    )
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT * FROM secrets", "submit-key")

    assert caught.value.code == "sql_object_not_allowed"
    assert caught.value.query_run is not None
    assert caught.value.query_run.status == "rejected"
    assert caught.value.query_run.id == "run-original"
    assert events == ["reserve"]


def test_same_key_with_different_sql_conflicts_without_a_new_run() -> None:
    events: list[str] = []
    repository = FakeRepository(events, replay=replay_reservation(fingerprint_matched=False))
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 2", "submit-key")

    assert caught.value.code == "idempotency_conflict"
    assert caught.value.query_run is None
    assert events == ["reserve"]


def test_invalid_idempotency_key_is_rejected_before_persistence() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 1", "key with spaces")

    assert caught.value.code == "invalid_idempotency_key"
    assert caught.value.query_run is None
    assert events == []


def test_submit_without_a_key_creates_a_new_run_every_time() -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    service = build_service(repository, PolicyDecision(True, None, None, ()))

    first = service.submit("SELECT 1")
    second = service.submit("SELECT 1")

    assert first.id != second.id
    assert events == ["reserve", "transition:queued", "reserve", "transition:queued"]
