"""Orchestrate policy, execution, and audit for query runs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.executor import ExecutionFailure, ExecutionSuccess, QueryExecutor
from app.models import QueryRun
from app.policy import PolicyDecision, SqlPolicy
from app.repository import QueryRunRepository


@dataclass
class SubmitResult:
    http_related_status: str  # succeeded | rejected | failed
    run: QueryRun
    columns: list[dict[str, str]] | None = None
    rows: list[list[Any]] | None = None


class QueryRunService:
    def __init__(
        self,
        session: Session,
        executor: QueryExecutor,
        policy: SqlPolicy | None = None,
    ) -> None:
        self._repo = QueryRunRepository(session)
        self._executor = executor
        self._policy = policy or SqlPolicy()

    def submit(self, sql: str) -> SubmitResult:
        started = time.perf_counter()
        run = self._repo.create_running(sql)

        policy_result = self._policy.check(sql)
        if policy_result.decision is PolicyDecision.REJECT:
            duration_ms = _elapsed_ms(started)
            self._repo.finalize(
                run,
                status="rejected",
                error_code=policy_result.error_code,
                error_message=policy_result.error_message,
                duration_ms=duration_ms,
            )
            return SubmitResult(http_related_status="rejected", run=run)

        outcome = self._executor.execute(sql)
        duration_ms = _elapsed_ms(started)

        if isinstance(outcome, ExecutionSuccess):
            self._repo.finalize(
                run,
                status="succeeded",
                row_count=outcome.row_count,
                duration_ms=duration_ms,
            )
            return SubmitResult(
                http_related_status="succeeded",
                run=run,
                columns=[{"name": c.name, "type": c.type} for c in outcome.columns],
                rows=outcome.rows,
            )

        assert isinstance(outcome, ExecutionFailure)
        self._repo.finalize(
            run,
            status="failed",
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            duration_ms=duration_ms,
        )
        return SubmitResult(http_related_status="failed", run=run)

    def get(self, run_id: uuid.UUID) -> QueryRun | None:
        return self._repo.get(run_id)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
