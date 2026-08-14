from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from decisionharbor_api.domain import QueryResult, RunState
from decisionharbor_api.policy import PolicyDecision
from decisionharbor_api.service import QueryRunService


@dataclass
class FakeAuditStore:
    calls: list[tuple[str, str]]

    def create(self, run_id, raw_sql, created_at):
        self.calls.append(("create", run_id))

    def reject(self, run_id, decision, finished_at):
        self.calls.append(("reject", run_id))

    def start(self, run_id, started_at):
        self.calls.append(("start", run_id))

    def succeed(self, run_id, result, finished_at):
        self.calls.append(("succeed", run_id))

    def fail(self, run_id, code, message, finished_at):
        self.calls.append(("fail", run_id))

    def get(self, run_id):
        return None


class FakeExecutor:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, sql):
        self.calls.append(sql)
        return QueryResult(({"name": "value", "type": "integer"},), ((1,),), 1, 3)


class FailingExecutor:
    def execute(self, sql):
        raise RuntimeError("password=secret host=db.internal stack trace")


def test_rejected_sql_is_audited_without_analytics_execution() -> None:
    audit = FakeAuditStore([])
    executor = FakeExecutor()
    service = QueryRunService(
        audit,
        executor,
        policy_evaluator=lambda sql: PolicyDecision(False, "object_not_allowed", "blocked"),
    )

    response = service.submit("SELECT * FROM platform.query_runs")

    assert response.state is RunState.REJECTED
    assert response.error_code == "object_not_allowed"
    assert executor.calls == []
    assert [name for name, _ in audit.calls] == ["create", "reject"]


def test_allowed_sql_records_execution_and_returns_result() -> None:
    audit = FakeAuditStore([])
    executor = FakeExecutor()
    first = datetime(2026, 8, 14, tzinfo=timezone.utc)
    ticks = iter([first, first + timedelta(milliseconds=2), first + timedelta(milliseconds=5)])
    service = QueryRunService(audit, executor, policy_evaluator=lambda sql: PolicyDecision(True, None, "ok"), clock=lambda: next(ticks))

    response = service.submit("SELECT 1")

    assert response.state is RunState.SUCCEEDED
    assert response.result is not None
    assert response.result.rows == ((1,),)
    assert response.duration_ms == 5
    assert [name for name, _ in audit.calls] == ["create", "start", "succeed"]


def test_unexpected_execution_errors_use_a_stable_public_message() -> None:
    audit = FakeAuditStore([])
    service = QueryRunService(
        audit,
        FailingExecutor(),
        policy_evaluator=lambda sql: PolicyDecision(True, None, "ok"),
    )

    response = service.submit("SELECT 1")

    assert response.state is RunState.FAILED
    assert response.error_code == "analytics_execution_error"
    assert response.error_message == "Analytics query execution failed"
