"""HTTP 端点。错误码与响应结构见 docs/design/api.md。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.runs.service import QueryRunService

router = APIRouter()


class QueryRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str


@router.post("/api/v1/query-runs")
def create_query_run(payload: QueryRunCreate, request: Request):
    service: QueryRunService = request.app.state.runs
    return service.submit(payload.sql)


@router.get("/api/v1/query-runs/{run_id}")
def get_query_run(run_id: str, request: Request):
    # 路径参数按字符串接收：非整数 id 属于「记录不存在」而非请求体错误，
    # 避免 RequestValidationError 全局处理把它误映射成 400。
    try:
        parsed_id = int(run_id)
    except ValueError:
        raise _run_not_found() from None
    service: QueryRunService = request.app.state.runs
    run = service.get(parsed_id)
    if run is None:
        raise _run_not_found()
    return {"run": run}


def _run_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "QY_RUN_NOT_FOUND", "message": "查询记录不存在"},
    )


def invalid_request_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    """非法请求体统一映射为 400 + 稳定错误码（不做逐字段错误回显）。"""
    return JSONResponse(
        status_code=400,
        content={
            "error": {"code": "QY_INVALID_REQUEST", "message": "请求体必须是含字符串 sql 字段的对象"}
        },
    )
