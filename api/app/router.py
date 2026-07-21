from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import PlatformSession
from app.executor import ExecutionError, ExecutionResult, execute_query
from app.models import QueryRun
from app.policy import check_policy

router = APIRouter(prefix="/api/v1")


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1)


class ErrorBody(BaseModel):
    code: str
    message: str


class QueryRunResponse(BaseModel):
    status: str
    data: dict | None = None
    error: ErrorBody | None = None


@router.post("/query-runs", status_code=201, response_model=QueryRunResponse)
def create_query_run(req: QueryRequest):
    verdict = check_policy(req.sql, max_rows=settings.query_max_rows)

    if not verdict.allowed:
        run = QueryRun(
            raw_sql=req.sql,
            status="rejected",
            reject_code=verdict.code,
            reject_reason=verdict.reason,
        )
        with PlatformSession() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        return QueryRunResponse(
            status="rejected",
            data={"id": run.id, "created_at": run.created_at.isoformat()},
            error=ErrorBody(code=verdict.code, message=verdict.reason),
        )

    result = execute_query(verdict.parsed_sql)

    if isinstance(result, ExecutionError):
        run = QueryRun(
            raw_sql=req.sql,
            status="failed",
            error_summary=result.message,
        )
        with PlatformSession() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        return QueryRunResponse(
            status="failed",
            data={"id": run.id, "created_at": run.created_at.isoformat()},
            error=ErrorBody(code=result.code, message=result.message),
        )

    run = QueryRun(
        raw_sql=req.sql,
        status="succeeded",
        row_count=result.row_count,
        columns_json=result.columns,
        duration_ms=result.duration_ms,
    )
    with PlatformSession() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    return QueryRunResponse(
        status="succeeded",
        data={
            "id": run.id,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "created_at": run.created_at.isoformat(),
        },
    )


@router.get("/query-runs/{run_id}", response_model=QueryRunResponse)
def get_query_run(run_id: int):
    with PlatformSession() as session:
        run = session.get(QueryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")

    if run.status == "succeeded":
        return QueryRunResponse(
            status="succeeded",
            data={
                "id": run.id,
                "columns": run.columns_json,
                "row_count": run.row_count,
                "duration_ms": run.duration_ms,
                "created_at": run.created_at.isoformat(),
            },
        )
    elif run.status == "rejected":
        return QueryRunResponse(
            status="rejected",
            data={"id": run.id, "created_at": run.created_at.isoformat()},
            error=ErrorBody(code=run.reject_code, message=run.reject_reason),
        )
    else:
        return QueryRunResponse(
            status="failed",
            data={"id": run.id, "created_at": run.created_at.isoformat()},
            error=ErrorBody(code="EXECUTION_ERROR", message=run.error_summary),
        )
