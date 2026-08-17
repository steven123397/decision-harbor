"""HTTP 端点。状态码语义见 ADR-0016：202 受理、422 策略拒绝、409 结果未就绪。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.runs.models import TERMINAL_STATES
from app.runs.service import QueryRunService

router = APIRouter()

QY_RESULT_NOT_READY = "QY_RESULT_NOT_READY"
QY_RESULT_NOT_AVAILABLE = "QY_RESULT_NOT_AVAILABLE"


class QueryRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str


@router.post("/api/v1/query-runs", status_code=202)
def create_query_run(payload: QueryRunCreate, request: Request):
    """合法输入立即受理（202）；策略拒绝同步落定（422），不入队。"""
    service: QueryRunService = request.app.state.runs
    outcome = service.submit(payload.sql)
    if outcome["outcome"] == "rejected":
        return JSONResponse(status_code=422, content={"run": outcome["run"]})
    return {"run": outcome["run"]}


@router.get("/api/v1/query-runs/{run_id}")
def get_query_run(run_id: str, request: Request):
    # 路径参数按字符串接收：非整数 id 属于「记录不存在」而非请求体错误，
    # 避免 RequestValidationError 全局处理把它误映射成 400。
    parsed_id = _parse_run_id(run_id)
    service: QueryRunService = request.app.state.runs
    run = service.get(parsed_id)
    if run is None:
        raise _run_not_found()
    return {"run": run}


@router.get("/api/v1/query-runs/{run_id}/result")
def get_query_run_result(run_id: str, request: Request):
    """读取结果快照，不重新执行 SQL（CONTEXT.md「结果快照」）。"""
    parsed_id = _parse_run_id(run_id)
    service: QueryRunService = request.app.state.runs
    data = service.snapshot(parsed_id)
    if data is None:
        raise _run_not_found()
    run, snap = data["run"], data["snapshot"]
    if run["state"] not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail={"code": QY_RESULT_NOT_READY, "message": "查询尚未完成，结果未就绪"},
        )
    if run["state"] != "succeeded" or snap is None:
        # 成功终态与快照同事务发布（ADR-0015）：succeeded 无快照属于
        # 不变量破坏，与 rejected/failed/cancelled 一样按不可用处理。
        raise HTTPException(
            status_code=409,
            detail={
                "code": QY_RESULT_NOT_AVAILABLE,
                "message": "该运行没有可读取的结果",
            },
        )
    return {
        "result": {
            "columns": snap.columns,
            "rows": snap.rows,
            "row_count": snap.row_count,
            "truncated": snap.truncated,
            "expires_at": snap.expires_at.isoformat(),
        }
    }


def _parse_run_id(run_id: str) -> int:
    try:
        return int(run_id)
    except ValueError:
        raise _run_not_found() from None


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
