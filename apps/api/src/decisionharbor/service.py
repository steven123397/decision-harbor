from dataclasses import dataclass
from datetime import datetime, timezone
from threading import BoundedSemaphore
from typing import Protocol

from decisionharbor.domain import QueryResult, QueryRun, finished_fields
from decisionharbor.executor import ExecutionFailure
from decisionharbor.policy import PolicyDecision


class Policy(Protocol):
    def evaluate(self, raw_sql: str) -> PolicyDecision: ...


class Repository(Protocol):
    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
    ) -> QueryRun: ...

    def transition(
        self,
        run_id: str,
        expected_status: str,
        **changes: object,
    ) -> QueryRun: ...


class Executor(Protocol):
    def execute(
        self,
        raw_sql: str,
        statement_timeout_ms: int,
        max_rows: int,
    ) -> QueryResult: ...


@dataclass(frozen=True)
class QueryOutcome:
    query_run: QueryRun
    result: QueryResult


class ServiceFailure(Exception):
    def __init__(self, code: str, message: str, query_run: QueryRun | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.query_run = query_run


class QueryRunService:
    def __init__(
        self,
        repository: Repository,
        policy: Policy,
        executor: Executor,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        max_concurrency: int,
        capacity_wait_ms: int,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._executor = executor
        self._policy_version = policy_version
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows
        self._capacity_wait_seconds = capacity_wait_ms / 1_000
        self._capacity = BoundedSemaphore(max_concurrency)

    def run(self, raw_sql: str) -> QueryOutcome:
        try:
            run = self._repository.create(
                raw_sql,
                self._policy_version,
                self._statement_timeout_ms,
                self._max_rows,
            )
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                None,
            ) from exc

        try:
            decision = self._policy.evaluate(raw_sql)
        except Exception as exc:
            failed = self._fail_received(run, "policy_internal_error", "The SQL policy could not be evaluated.")
            raise ServiceFailure(
                "policy_internal_error",
                "The SQL policy could not be evaluated.",
                failed,
            ) from exc

        if not decision.allowed:
            rejected = self._transition(
                run,
                "received",
                status="rejected",
                policy_decision="rejected",
                referenced_objects=decision.referenced_objects,
                error_code=decision.code,
                error_summary=decision.summary,
                **finished_fields(run),
            )
            raise ServiceFailure(decision.code or "unsupported_sql", decision.summary or "SQL is not allowed.", rejected)

        if not self._capacity.acquire(timeout=self._capacity_wait_seconds):
            failed = self._fail_received(
                run,
                "query_capacity_exceeded",
                "Query capacity is currently exhausted.",
                policy_decision="allowed",
                referenced_objects=decision.referenced_objects,
            )
            raise ServiceFailure("query_capacity_exceeded", failed.error_summary or "Query capacity is currently exhausted.", failed)

        try:
            running = self._transition(
                run,
                "received",
                status="running",
                policy_decision="allowed",
                referenced_objects=decision.referenced_objects,
                started_at=datetime.now(timezone.utc),
            )
            try:
                result = self._executor.execute(
                    raw_sql,
                    self._statement_timeout_ms,
                    self._max_rows,
                )
            except ExecutionFailure as exc:
                failed = self._transition(
                    running,
                    "running",
                    status="failed",
                    error_code=exc.code,
                    error_summary=exc.message,
                    **finished_fields(running),
                )
                raise ServiceFailure(exc.code, exc.message, failed) from exc
            except Exception as exc:
                failed = self._transition(
                    running,
                    "running",
                    status="failed",
                    error_code="internal_error",
                    error_summary="The query could not be completed.",
                    **finished_fields(running),
                )
                raise ServiceFailure("internal_error", "The query could not be completed.", failed) from exc

            succeeded = self._transition(
                running,
                "running",
                status="succeeded",
                returned_row_count=len(result.rows),
                result_truncated=result.truncated,
                **finished_fields(running),
            )
            return QueryOutcome(query_run=succeeded, result=result)
        finally:
            self._capacity.release()

    def _fail_received(
        self,
        run: QueryRun,
        code: str,
        summary: str,
        **facts: object,
    ) -> QueryRun:
        return self._transition(
            run,
            "received",
            status="failed",
            error_code=code,
            error_summary=summary,
            **facts,
            **finished_fields(run),
        )

    def _transition(
        self,
        run: QueryRun,
        expected_status: str,
        **changes: object,
    ) -> QueryRun:
        try:
            return self._repository.transition(run.id, expected_status, **changes)
        except ServiceFailure:
            raise
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                run,
            ) from exc
