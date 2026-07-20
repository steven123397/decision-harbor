"""查询运行路由：提交与读取。流程对应 docs/design/overview.md 的提交链路。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app import audit
from app.db import platform_session
from app.errors import INVALID_REQUEST, RECORD_NOT_FOUND, ApiError
from app.errors import INTERNAL_ERROR
from app.executor import ExecutionFailure, execute
from app.models import STATUS_FAILED, STATUS_REJECTED, STATUS_SUCCEEDED
from app.policy import evaluate
from app.schemas import (
    QueryRequest,
    QueryRunResponse,
    RecordOut,
    RecordResponse,
    ResultOut,
)

router = APIRouter()


@router.post("/api/v1/query-runs", response_model=QueryRunResponse)
def submit_query_run(payload: QueryRequest) -> QueryRunResponse:
    sql = payload.sql
    if not sql.strip():
        raise ApiError(400, INVALID_REQUEST, "sql 必须为非空字符串。")
    session = platform_session()
    try:
        run = audit.create_run(session, sql)

        decision = evaluate(sql)
        if not decision.allowed:
            run = audit.finalize_run(
                session,
                run,
                status=STATUS_REJECTED,
                error_code=decision.error_code,
                error_message=decision.message,
            )
            return QueryRunResponse(record=RecordOut.from_run(run), result=None)

        try:
            result = execute(sql)
        except ExecutionFailure as exc:
            run = audit.finalize_run(
                session, run, status=STATUS_FAILED,
                error_code=exc.code, error_message=exc.message,
            )
            return QueryRunResponse(record=RecordOut.from_run(run), result=None)
        except Exception:
            run = audit.finalize_run(
                session, run, status=STATUS_FAILED,
                error_code=INTERNAL_ERROR, error_message="未预期的内部错误。",
            )
            return QueryRunResponse(record=RecordOut.from_run(run), result=None)

        run = audit.finalize_run(
            session, run, status=STATUS_SUCCEEDED,
            row_count=result.row_count, duration_ms=result.duration_ms,
        )
        return QueryRunResponse(
            record=RecordOut.from_run(run),
            result=ResultOut(
                columns=result.columns, rows=result.rows, truncated=result.truncated
            ),
        )
    finally:
        session.close()


@router.get("/api/v1/query-runs/{run_id}", response_model=RecordResponse)
def get_query_run(run_id: uuid.UUID) -> RecordResponse:
    session = platform_session()
    try:
        run = audit.get_run(session, run_id)
        if run is None:
            raise ApiError(404, RECORD_NOT_FOUND, "查询记录不存在。")
        return RecordResponse(record=RecordOut.from_run(run))
    finally:
        session.close()
