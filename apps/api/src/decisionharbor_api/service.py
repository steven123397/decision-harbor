from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from .domain import (
    AuditPersistenceError,
    ExecutionFailure,
    QueryResult,
    QueryRunResponse,
    RunState,
)
from .policy import PolicyDecision, evaluate_sql


class AuditStore(Protocol):
    def create(self, run_id: str, raw_sql: str, created_at: datetime) -> None: ...

    def reject(self, run_id: str, decision: PolicyDecision, finished_at: datetime) -> None: ...

    def start(self, run_id: str, started_at: datetime) -> None: ...

    def succeed(self, run_id: str, result: QueryResult, finished_at: datetime) -> None: ...

    def fail(self, run_id: str, code: str, message: str, finished_at: datetime) -> None: ...

    def get(self, run_id: str) -> QueryRunResponse | None: ...


class AnalyticsExecutor(Protocol):
    def execute(self, sql: str) -> QueryResult: ...


class QueryRunService:
    def __init__(
        self,
        audit_store: AuditStore,
        executor: AnalyticsExecutor,
        policy_evaluator: Callable[[str], PolicyDecision] = evaluate_sql,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._audit_store = audit_store
        self._executor = executor
        self._policy_evaluator = policy_evaluator
        self._clock = clock

    def submit(self, raw_sql: str) -> QueryRunResponse:
        run_id = str(uuid4())
        created_at = self._clock()
        self._audit_store.create(run_id, raw_sql, created_at)

        decision = self._policy_evaluator(raw_sql)
        if not decision.allowed:
            finished_at = self._clock()
            self._audit_store.reject(run_id, decision, finished_at)
            return QueryRunResponse(
                run_id=run_id,
                raw_sql=raw_sql,
                state=RunState.REJECTED,
                outcome=RunState.REJECTED,
                created_at=created_at,
                policy_decision="rejected",
                policy_code=decision.code,
                duration_ms=_duration_ms(created_at, finished_at),
                error_code=decision.code,
                error_message=decision.message,
            )

        started_at = self._clock()
        self._audit_store.start(run_id, started_at)
        try:
            result = self._executor.execute(raw_sql)
        except ExecutionFailure as error:
            finished_at = self._clock()
            self._audit_store.fail(run_id, error.code, error.message, finished_at)
            return QueryRunResponse(
                run_id=run_id,
                raw_sql=raw_sql,
                state=RunState.FAILED,
                outcome=RunState.FAILED,
                created_at=created_at,
                policy_decision="allowed",
                row_count=None,
                duration_ms=_duration_ms(created_at, finished_at),
                error_code=error.code,
                error_message=error.message,
            )
        except Exception as error:  # pragma: no cover - defensive boundary
            finished_at = self._clock()
            message = "Analytics query execution failed"
            self._audit_store.fail(run_id, "analytics_execution_error", message, finished_at)
            return QueryRunResponse(
                run_id=run_id,
                raw_sql=raw_sql,
                state=RunState.FAILED,
                outcome=RunState.FAILED,
                created_at=created_at,
                policy_decision="allowed",
                duration_ms=_duration_ms(created_at, finished_at),
                error_code="analytics_execution_error",
                error_message=message,
            )

        finished_at = self._clock()
        try:
            self._audit_store.succeed(run_id, result, finished_at)
        except AuditPersistenceError:
            raise
        return QueryRunResponse(
            run_id=run_id,
            raw_sql=raw_sql,
            state=RunState.SUCCEEDED,
            outcome=RunState.SUCCEEDED,
            created_at=created_at,
            policy_decision="allowed",
            row_count=result.row_count,
            duration_ms=_duration_ms(created_at, finished_at),
            result=result,
        )

    def get(self, run_id: str) -> QueryRunResponse | None:
        return self._audit_store.get(run_id)


def _duration_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))
