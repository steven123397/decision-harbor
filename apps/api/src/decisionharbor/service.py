from typing import Protocol

from decisionharbor.domain import (
    CANCEL_OUTCOME_NOT_CANCELLABLE,
    CANCEL_OUTCOME_NOT_FOUND,
    RESULT_EXPIRED,
    RESULT_NOT_READY,
    RESULT_UNAVAILABLE,
    QueryResult,
    QueryRun,
    QueryRunCreation,
    StoredResult,
    finished_fields,
    result_read_failure,
)
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
        idempotency_key: str | None = None,
    ) -> QueryRunCreation: ...

    def transition(
        self,
        run_id: str,
        expected_status: str,
        **changes: object,
    ) -> QueryRun: ...

    def get(self, run_id: str) -> QueryRun | None: ...

    def cancel(self, run_id: str) -> tuple[str, QueryRun | None]: ...

    def get_result_state(self, run_id: str) -> StoredResult | None: ...


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
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._policy_version = policy_version
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows

    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun:
        try:
            creation = self._repository.create(
                raw_sql,
                self._policy_version,
                self._statement_timeout_ms,
                self._max_rows,
                idempotency_key,
            )
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                None,
            ) from exc

        run = creation.query_run
        if not creation.created:
            if run.raw_sql != raw_sql:
                raise ServiceFailure(
                    "idempotency_conflict",
                    "Idempotency-Key was already used with a different request.",
                    None,
                )
            if run.status != "received":
                return self._replay(run)

        try:
            decision = self._policy.evaluate(raw_sql)
        except Exception as exc:
            failed = self._fail_received(
                run,
                "policy_internal_error",
                "The SQL policy could not be evaluated.",
                replay_on_conflict=idempotency_key is not None,
            )
            if failed.status != "failed":
                return self._replay(failed)
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
                replay_on_conflict=idempotency_key is not None,
                **finished_fields(run),
            )
            return self._replay(rejected)

        return self._replay(
            self._transition(
                run,
                "received",
                status="queued",
                policy_decision="allowed",
                referenced_objects=decision.referenced_objects,
                replay_on_conflict=idempotency_key is not None,
            )
        )

    def read_result(self, run_id: str) -> QueryResult:
        """Read a retained snapshot without re-executing the query."""

        try:
            stored = self._repository.get_result_state(run_id)
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                None,
            ) from exc
        if stored is None:
            raise ServiceFailure("query_run_not_found", "Query run was not found.", None)

        failure = result_read_failure(
            stored.run,
            has_snapshot=stored.snapshot is not None,
            expired=stored.result_expired,
        )
        if failure is not None:
            messages = {
                RESULT_NOT_READY: "The query result is not ready yet.",
                RESULT_UNAVAILABLE: "This query run has no readable result.",
                RESULT_EXPIRED: "The query result has expired.",
            }
            raise ServiceFailure(failure, messages[failure], stored.run)
        assert stored.snapshot is not None
        return stored.snapshot

    def cancel(self, run_id: str) -> tuple[str, QueryRun]:
        try:
            outcome, run = self._repository.cancel(run_id)
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                None,
            ) from exc
        if outcome == CANCEL_OUTCOME_NOT_FOUND:
            raise ServiceFailure("query_run_not_found", "Query run was not found.", None)
        if outcome == CANCEL_OUTCOME_NOT_CANCELLABLE:
            raise ServiceFailure(
                "query_run_not_cancellable",
                "The query run cannot be cancelled.",
                run,
            )
        assert run is not None
        return outcome, run

    def _replay(self, run: QueryRun) -> QueryRun:
        if run.status == "rejected":
            raise ServiceFailure(
                run.error_code or "unsupported_sql",
                run.error_summary or "SQL is not allowed.",
                run,
            )
        return run

    def _fail_received(
        self,
        run: QueryRun,
        code: str,
        summary: str,
        replay_on_conflict: bool = False,
        **facts: object,
    ) -> QueryRun:
        return self._transition(
            run,
            "received",
            status="failed",
            error_code=code,
            error_summary=summary,
            replay_on_conflict=replay_on_conflict,
            **facts,
            **finished_fields(run),
        )

    def _transition(
        self,
        run: QueryRun,
        expected_status: str,
        replay_on_conflict: bool = False,
        **changes: object,
    ) -> QueryRun:
        try:
            return self._repository.transition(run.id, expected_status, **changes)
        except ServiceFailure:
            raise
        except Exception as exc:
            if replay_on_conflict:
                try:
                    current = self._repository.get(run.id)
                except Exception:
                    current = None
                if current is not None and current.status != expected_status:
                    return current
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                run,
            ) from exc
