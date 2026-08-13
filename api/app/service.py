from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.errors import ExecutionFailed
from app.executor import QueryExecutor
from app.models import QueryRun
from app.policy import evaluate_sql
from app.schemas import ErrorBody, QueryResult, QueryRunResponse, ResultColumn


class QueryRunService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        executor: QueryExecutor,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._executor = executor
        self._settings = settings

    def create(self, sql: str) -> QueryRunResponse:
        started = time.perf_counter()
        run = QueryRun(
            id=uuid.uuid4(),
            sql_text=sql,
            status="running",
            created_at=datetime.now(timezone.utc),
        )
        with self._session_factory() as session:
            session.add(run)
            session.commit()
            session.refresh(run)

            if len(sql) > self._settings.query_max_sql_chars:
                self._finalize(
                    session,
                    run,
                    status="rejected",
                    error_code="QUERY_TOO_LARGE",
                    error_message="SQL exceeds the maximum allowed length.",
                    started=started,
                )
                return self._to_response(run, include_result=True)

            decision = evaluate_sql(sql)
            if not decision.allowed:
                self._finalize(
                    session,
                    run,
                    status="rejected",
                    error_code=decision.code or "POLICY_DENIED",
                    error_message=decision.message or "Query was rejected by policy.",
                    started=started,
                )
                return self._to_response(run, include_result=True)

            try:
                result = self._executor.execute(sql)
            except ExecutionFailed as exc:
                self._finalize(
                    session,
                    run,
                    status="failed",
                    error_code=exc.code,
                    error_message=exc.message,
                    started=started,
                )
                return self._to_response(run, include_result=True)

            self._finalize(
                session,
                run,
                status="succeeded",
                row_count=result.row_count,
                started=started,
            )
            return self._to_response(run, include_result=True, result=result)

    def get(self, run_id: uuid.UUID) -> QueryRun | None:
        with self._session_factory() as session:
            return session.get(QueryRun, run_id)

    def _finalize(
        self,
        session: Session,
        run: QueryRun,
        *,
        status: str,
        started: float,
        error_code: str | None = None,
        error_message: str | None = None,
        row_count: int | None = None,
    ) -> None:
        run.status = status
        run.error_code = error_code
        run.error_message = error_message
        run.row_count = row_count
        run.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        session.add(run)
        session.commit()
        session.refresh(run)

    def _to_response(
        self,
        run: QueryRun,
        *,
        include_result: bool,
        result: object | None = None,
    ) -> QueryRunResponse:
        payload_result = None
        if include_result and result is not None and run.status == "succeeded":
            payload_result = QueryResult(
                columns=[ResultColumn(name=col["name"], type=col["type"]) for col in result.columns],
                rows=result.rows,
                row_count=result.row_count,
            )
        error = None
        if run.error_code:
            error = ErrorBody(code=run.error_code, message=run.error_message or "")
        return QueryRunResponse(
            id=run.id,
            status=run.status,  # type: ignore[arg-type]
            sql=run.sql_text,
            result=payload_result,
            error=error,
            duration_ms=run.duration_ms,
            row_count=run.row_count,
            created_at=run.created_at,
        )


def to_audit_response(run: QueryRun) -> QueryRunResponse:
    error = None
    if run.error_code:
        error = ErrorBody(code=run.error_code, message=run.error_message or "")
    return QueryRunResponse(
        id=run.id,
        status=run.status,  # type: ignore[arg-type]
        sql=run.sql_text,
        result=None,
        error=error,
        duration_ms=run.duration_ms,
        row_count=run.row_count,
        created_at=run.created_at,
    )
