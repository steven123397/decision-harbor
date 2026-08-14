"""HTTP 端点。错误码与响应结构见 docs/design/api.md。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel

from app.runs.service import QueryRunService

router = APIRouter()


class QueryRunCreate(BaseModel):
    sql: str


@router.post("/api/v1/query-runs")
def create_query_run(payload: QueryRunCreate, request: Request):
    service: QueryRunService = request.app.state.runs
    return service.submit(payload.sql)


@router.get("/api/v1/query-runs/{run_id}")
def get_query_run(run_id: int, request: Request):
    service: QueryRunService = request.app.state.runs
    run = service.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "QY_RUN_NOT_FOUND", "message": "查询记录不存在"},
        )
    return {"run": run}
