from typing import Protocol

from decisionharbor.domain import (
    SUBMIT_IDEMPOTENCY_SCOPE,
    IdempotencyClaim,
    QueryRun,
    SubmitReservation,
    finished_fields,
    is_valid_idempotency_key,
    submit_request_fingerprint,
)
from decisionharbor.policy import PolicyDecision


class Policy(Protocol):
    def evaluate(self, raw_sql: str) -> PolicyDecision: ...


class Repository(Protocol):
    def reserve(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        *,
        idempotency: IdempotencyClaim | None,
    ) -> SubmitReservation: ...

    def transition(
        self,
        run_id: str,
        expected_status: str,
        **changes: object,
    ) -> QueryRun: ...


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
        if idempotency_key is not None and not is_valid_idempotency_key(idempotency_key):
            raise ServiceFailure(
                "invalid_idempotency_key",
                "The Idempotency-Key header is invalid.",
                None,
            )
        try:
            reservation = self._repository.reserve(
                raw_sql,
                self._policy_version,
                self._statement_timeout_ms,
                self._max_rows,
                idempotency=self._claim(raw_sql, idempotency_key),
            )
        except Exception as exc:
            raise ServiceFailure(
                "audit_unavailable",
                "The audit store is unavailable.",
                None,
            ) from exc

        if reservation.is_replay:
            if not reservation.fingerprint_matched:
                raise ServiceFailure(
                    "idempotency_conflict",
                    "The idempotency key was already used with a different request.",
                    None,
                )
            replayed = reservation.run
            if replayed.status == "rejected":
                raise ServiceFailure(
                    replayed.error_code or "unsupported_sql",
                    replayed.error_summary or "SQL is not allowed.",
                    replayed,
                )
            return replayed

        run = reservation.run
        try:
            decision = self._policy.evaluate(raw_sql)
        except Exception as exc:
            failed = self._transition(
                run,
                "received",
                status="failed",
                error_code="policy_internal_error",
                error_summary="The SQL policy could not be evaluated.",
                **finished_fields(run),
            )
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

        return self._transition(
            run,
            "received",
            status="queued",
            policy_decision="allowed",
            referenced_objects=decision.referenced_objects,
        )

    def _claim(self, raw_sql: str, idempotency_key: str | None) -> IdempotencyClaim | None:
        if idempotency_key is None:
            return None
        return IdempotencyClaim(
            scope=SUBMIT_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_fingerprint=submit_request_fingerprint(raw_sql),
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
