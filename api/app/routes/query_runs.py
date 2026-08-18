"""HTTP 端点。状态码语义见 ADR-0018：读 200 携带 outcome、202 受理、
422 策略拒绝、409 生命周期转移冲突（幂等冲突、结果未就绪）。"""

from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.runs.models import TERMINAL_STATES
from app.runs.service import QueryRunService

router = APIRouter()

QY_RESULT_NOT_READY = "QY_RESULT_NOT_READY"
QY_RESULT_NOT_AVAILABLE = "QY_RESULT_NOT_AVAILABLE"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
RUN_NOT_CANCELLABLE = "run_not_cancellable"
RUN_NOT_RETRYABLE = "run_not_retryable"

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


class InvalidQueryParam(HTTPException):
    """查询参数非法：渲染为与非法请求体同形的 400 信封（main.py 注册
    专属处理器），不与 404/409 的 detail 形状混同。"""

    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=400,
            detail={"code": "QY_INVALID_REQUEST", "message": message},
        )


class QueryRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


@router.post("/api/v1/query-runs")
def create_query_run(payload: QueryRunCreate, request: Request):
    """合法输入立即受理（202）；策略拒绝同步落定（422），不入队；同键同
    SQL 重放返回原运行（200）；同键异 SQL 是转移冲突（409，ADR-0018）。"""
    service: QueryRunService = request.app.state.runs
    outcome = service.submit(payload.sql, idempotency_key=payload.idempotency_key)
    if outcome["outcome"] == "rejected":
        return JSONResponse(status_code=422, content={"run": outcome["run"]})
    if outcome["outcome"] == "replayed":
        # 重放不再产生新的执行任务，200 与「读操作」同层；响应体与 202 相同。
        return JSONResponse(status_code=200, content={"run": outcome["run"]})
    if outcome["outcome"] == "conflict":
        bound = outcome["run"]
        raise HTTPException(
            status_code=409,
            detail={
                "code": IDEMPOTENCY_CONFLICT,
                "message": f"该幂等键已绑定运行 {bound['id']} 的不同 SQL",
            },
        )
    return JSONResponse(status_code=202, content={"run": outcome["run"]})


@router.get("/api/v1/query-runs")
def list_query_runs(request: Request, limit: str | None = None, cursor: str | None = None):
    """历史列表：按创建时间（created_at、id 双键降序）游标分页。

    limit 与 cursor 手工解析：非法取值返回与非法请求体同形的 400（信封
    {"error": {code, message}}，见 invalid_request_handler），不借道
    FastAPI 校验器（同 _parse_run_id 的理由）。
    """
    parsed_limit = _parse_limit(limit)
    parsed_cursor = _parse_cursor(cursor)
    service: QueryRunService = request.app.state.runs
    page = service.list_runs(limit=parsed_limit, cursor=parsed_cursor)
    return {"runs": page["runs"], "next_cursor": _encode_cursor(page["next_cursor"])}


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


@router.post("/api/v1/query-runs/{run_id}/cancel")
def cancel_query_run(run_id: str, request: Request):
    """取消：排队中确定生效（200 + cancelled）；运行中受理为 cancelling
    （202，best effort 中止底层查询）；已 cancelled 幂等 200；其余终态
    409 run_not_cancellable（ADR-0018）。"""
    parsed_id = _parse_run_id(run_id)
    service: QueryRunService = request.app.state.runs
    result = service.cancel(parsed_id)
    if result is None:
        raise _run_not_found()
    outcome, run = result["outcome"], result["run"]
    if outcome == "not_cancellable":
        raise HTTPException(
            status_code=409,
            detail={"code": RUN_NOT_CANCELLABLE, "message": result["message"]},
        )
    if outcome == "cancelling":
        # 转移已受理、终态尚未落定：与受理语义同层的 202
        return JSONResponse(status_code=202, content={"run": run})
    return JSONResponse(status_code=200, content={"run": run})


@router.post("/api/v1/query-runs/{run_id}/retry")
def retry_query_run(run_id: str, request: Request):
    """重试：仅 failed / cancelled → 202 + 新运行（retry_of 指向原运行）；
    rejected 的 409 附「修改 SQL 后重新提交」指引；其余状态一律 409
    run_not_retryable（ADR-0018）。"""
    parsed_id = _parse_run_id(run_id)
    service: QueryRunService = request.app.state.runs
    result = service.retry(parsed_id)
    if result is None:
        raise _run_not_found()
    if result["outcome"] == "not_retryable":
        raise HTTPException(
            status_code=409,
            detail={"code": RUN_NOT_RETRYABLE, "message": result["message"]},
        )
    return JSONResponse(status_code=202, content={"run": result["run"]})


def _parse_run_id(run_id: str) -> int:
    try:
        return int(run_id)
    except ValueError:
        raise _run_not_found() from None


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_PAGE_LIMIT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_PAGE_LIMIT:
        raise InvalidQueryParam(f"limit 必须是 1 至 {MAX_PAGE_LIMIT} 的整数")
    return value


def _parse_cursor(raw: str | None) -> tuple[datetime, int] | None:
    if raw is None:
        return None
    try:
        # 游标对客户端不透明：base64url(创建时间 iso | 运行 id)。
        # binascii / UnicodeDecode / fromisoformat 的错误都是 ValueError 子类。
        decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        ts_str, _, id_str = decoded.rpartition("|")
        return datetime.fromisoformat(ts_str), int(id_str)
    except ValueError:
        raise InvalidQueryParam("cursor 不是有效的分页游标") from None


def _encode_cursor(cursor: tuple[datetime, int] | None) -> str | None:
    if cursor is None:
        return None
    ts, run_id = cursor
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{run_id}".encode()).decode("ascii")


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
