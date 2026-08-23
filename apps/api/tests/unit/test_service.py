from dataclasses import replace

import pytest

from decisionharbor.domain import IdempotencyRecord, QueryRun
from decisionharbor.policy import PolicyDecision
from decisionharbor.repository import IdempotencyKeyTaken
from decisionharbor.service import QueryRunService, ServiceFailure, request_fingerprint


class FakePolicy:
    def __init__(self, decision: PolicyDecision, *, fail: bool = False) -> None:
        self.decision = decision
        self.fail = fail

    def evaluate(self, raw_sql: str) -> PolicyDecision:
        if self.fail:
            raise RuntimeError("sqlglot internals must not escape")
        return self.decision


class FakeRepository:
    def __init__(
        self,
        events: list[str],
        *,
        fail_create: bool = False,
        delayed_key_record: IdempotencyRecord | None = None,
        fail_cancel: bool = False,
        cancel_result: tuple[str, QueryRun | None] | None = None,
    ) -> None:
        self.events = events
        self.fail_create = fail_create
        # 模拟并发竞态：另一事务的键记录在首次读取后才可见。
        self.delayed_key_record = delayed_key_record
        self.fail_cancel = fail_cancel
        self.cancel_result = cancel_result
        self.reveal_delayed = False
        self.run = QueryRun.received(
            raw_sql="SELECT 1",
            policy_version="policy-v1",
            statement_timeout_ms=5_000,
            max_rows=500,
        )
        self.runs: dict[str, QueryRun] = {}
        self.idempotency: dict[tuple[str, str], IdempotencyRecord] = {}

    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        idempotency_key: str | None = None,
    ) -> QueryRun:
        self.events.append("create")
        if self.fail_create:
            raise RuntimeError("database DSN and secret must not escape")
        # 每次创建产生新的 received 运行；事务失败（约束触发）时随事务回滚，
        # 因此只有在 create 成功返回后才登记运行。
        self.run = QueryRun.received(
            raw_sql=raw_sql,
            policy_version=policy_version,
            statement_timeout_ms=statement_timeout_ms,
            max_rows=max_rows,
        )
        if idempotency_key is not None:
            if self.delayed_key_record is not None and not self.reveal_delayed:
                # 并发竞态：另一事务已提交同一键，本事务创建时唯一约束失败。
                self.reveal_delayed = True
                raise IdempotencyKeyTaken()
            record = IdempotencyRecord(
                scope="submit",
                key=idempotency_key,
                request_fingerprint=request_fingerprint(raw_sql),
                run_id=self.run.id,
            )
            if (record.scope, record.key) in self.idempotency:
                # 与真实数据库一致：并发提交同一键时唯一约束触发。
                raise IdempotencyKeyTaken()
            self.idempotency[(record.scope, record.key)] = record
        self.runs[self.run.id] = self.run
        return self.run

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        self.events.append(f"transition:{changes['status']}")
        assert self.run.status == expected_status
        self.run = replace(self.run, **changes)
        self.runs[self.run.id] = self.run
        return self.run

    def get(self, run_id: str) -> QueryRun | None:
        return self.runs.get(run_id)

    def find_idempotency(self, scope: str, key: str) -> IdempotencyRecord | None:
        if self.delayed_key_record is not None and self.reveal_delayed:
            return self.delayed_key_record
        return self.idempotency.get((scope, key))

    def cancel(self, run_id: str) -> tuple[str, QueryRun | None]:
        self.events.append("cancel")
        if self.fail_cancel:
            raise RuntimeError("database DSN and secret must not escape")
        if self.cancel_result is not None:
            return self.cancel_result
        return ("cancelled", self.run)


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


REJECTED = PolicyDecision(False, "sql_object_not_allowed", "Object is not allowed.", ())


def test_replay_with_the_same_key_and_input_returns_the_original_run_without_new_work() -> None:
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    first = service.submit("SELECT 1", idempotency_key="key-1")
    replay = service.submit("SELECT 1", idempotency_key="key-1")

    assert replay.id == first.id
    assert replay.status == "queued"
    # 只有一次创建：重放不产生新的查询运行或重复执行。
    assert repository.events.count("create") == 1


def test_replay_of_a_rejected_run_reraises_the_original_rejection() -> None:
    repository = FakeRepository([])
    service = build_service(repository, REJECTED)

    with pytest.raises(ServiceFailure) as first:
        service.submit("SELECT * FROM secrets", idempotency_key="key-1")
    with pytest.raises(ServiceFailure) as replay:
        service.submit("SELECT * FROM secrets", idempotency_key="key-1")

    assert replay.value.code == "sql_object_not_allowed"
    assert replay.value.query_run is not None
    assert replay.value.query_run.id == first.value.query_run.id
    assert replay.value.query_run.status == "rejected"
    assert repository.events.count("create") == 1


def test_same_key_with_different_input_conflicts() -> None:
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    service.submit("SELECT 1", idempotency_key="key-1")

    with pytest.raises(ServiceFailure) as caught:
        service.submit("SELECT 2", idempotency_key="key-1")

    assert caught.value.code == "idempotency_conflict"
    assert caught.value.query_run is None
    assert repository.events.count("create") == 1


def test_missing_key_always_creates_a_new_run() -> None:
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    first = service.submit("SELECT 1")
    second = service.submit("SELECT 1")

    assert first.id != second.id
    assert repository.events.count("create") == 2


def test_concurrent_create_race_falls_back_to_the_winning_record() -> None:
    # 另一并发请求先赢得了同一键：其运行已入队，记录在竞态窗口内对本请求不可见。
    winner_run = QueryRun.received("SELECT 1", "policy-v1", 5_000, 500)
    winner_run = replace(winner_run, status="queued", policy_decision="allowed")
    delayed = IdempotencyRecord(
        scope="submit",
        key="key-1",
        request_fingerprint=request_fingerprint("SELECT 1"),
        run_id=winner_run.id,
    )
    repository = FakeRepository([], delayed_key_record=delayed)
    repository.runs[winner_run.id] = winner_run
    service = build_service(repository, ALLOWED)

    # 本请求预检未命中，创建时撞上唯一约束，随后重读命中先到的记录并按重放返回。
    replay = service.submit("SELECT 1", idempotency_key="key-1")

    assert replay.id == winner_run.id
    assert replay.status == "queued"
    assert repository.events.count("create") == 1
    assert "transition:queued" not in repository.events
    assert len(repository.runs) == 1


def test_replay_of_a_finished_run_returns_the_same_run_without_new_work() -> None:
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    first = service.submit("SELECT 1", idempotency_key="key-1")
    # 模拟 Worker 已把该运行推进为终态。
    repository.runs[first.id] = replace(repository.runs[first.id], status="succeeded", returned_row_count=1)
    replay = service.submit("SELECT 1", idempotency_key="key-1")

    assert replay.id == first.id
    assert replay.status == "succeeded"
    assert repository.events.count("create") == 1


def test_replay_during_execution_returns_the_original_run() -> None:
    # 网络重试可能落在 Worker 已领取运行之后（running/cancelling）。
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    first = service.submit("SELECT 1", idempotency_key="key-1")
    repository.runs[first.id] = replace(repository.runs[first.id], status="running", started_at=repository.runs[first.id].created_at)
    replay = service.submit("SELECT 1", idempotency_key="key-1")

    assert replay.id == first.id
    assert replay.status == "running"
    assert repository.events.count("create") == 1


def test_request_fingerprint_is_stable_and_input_sensitive() -> None:
    assert request_fingerprint("SELECT 1") == request_fingerprint("SELECT 1")
    assert request_fingerprint("SELECT 1") != request_fingerprint("SELECT 2")


def test_cancel_forwards_the_repository_outcome_and_run() -> None:
    repository = FakeRepository([])
    service = build_service(repository, ALLOWED)

    outcome, run = service.cancel(repository.run.id)

    assert outcome == "cancelled"
    assert run is repository.run
    assert repository.events == ["cancel"]


def test_cancel_of_a_missing_run_fails_with_query_run_not_found() -> None:
    repository = FakeRepository([], cancel_result=("not_found", None))
    service = build_service(repository, ALLOWED)

    with pytest.raises(ServiceFailure) as caught:
        service.cancel("missing-run")

    assert caught.value.code == "query_run_not_found"
    assert caught.value.query_run is None


def test_cancel_of_a_not_cancellable_run_attaches_the_existing_run() -> None:
    failed = replace(
        QueryRun.received("SELECT 1", "policy-v1", 5_000, 500),
        status="failed",
        error_code="query_semantic_error",
    )
    repository = FakeRepository([], cancel_result=("not_cancellable", failed))
    service = build_service(repository, ALLOWED)

    with pytest.raises(ServiceFailure) as caught:
        service.cancel(failed.id)

    assert caught.value.code == "query_run_not_cancellable"
    assert caught.value.query_run is failed


def test_cancel_repository_failure_maps_to_audit_unavailable() -> None:
    repository = FakeRepository([], fail_cancel=True)
    service = build_service(repository, ALLOWED)

    with pytest.raises(ServiceFailure) as caught:
        service.cancel("any-run")

    assert caught.value.code == "audit_unavailable"
    assert "DSN" not in caught.value.message
