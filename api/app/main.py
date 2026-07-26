"""DecisionHarbor API：受治理查询链路的 HTTP 入口。

端点语义、错误码注册表与响应结构见 docs/design/query-runs-api.md；
处理顺序见 docs/design/architecture.md 数据流。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import executor, query_runs
from app.config import get_settings
from app.db import analytics_reader_engine, platform_engine
from app.policy import evaluate
from app.readiness import check_readiness

logger = logging.getLogger("decisionharbor.api")

app = FastAPI(title="DecisionHarbor API", version="0.1.0")


class QueryRunRequest(BaseModel):
    sql: str


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    return _error_response(422, "invalid_request", f"请求不合法：{error.errors()[0]['msg']}")


@app.exception_handler(Exception)
async def on_unhandled_error(_: Request, error: Exception) -> JSONResponse:
    logger.exception("internal error", exc_info=error)
    return _error_response(500, "internal_error", "平台内部错误，请求未完成")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    problems = check_readiness(platform_engine(), analytics_reader_engine())
    if problems:
        return JSONResponse(status_code=503, content={"status": "unavailable", "problems": problems})
    return JSONResponse(status_code=200, content={"status": "ready"})


@app.post("/api/v1/query-runs", status_code=201)
def submit_query(request: QueryRunRequest) -> Any:
    settings = get_settings()
    if len(request.sql) > settings.query_max_sql_length:
        # 超长输入不入审计，防止审计存储被恶意大输入撑爆（见设计）
        return _error_response(
            422,
            "invalid_request",
            f"SQL 超过长度上限 {settings.query_max_sql_length} 字符",
        )

    platform = platform_engine()
    run = query_runs.create_run(platform, request.sql)

    decision = evaluate(request.sql)
    if not decision.allowed:
        assert decision.error_code is not None and decision.error_message is not None
        query_runs.finish_run(
            platform,
            run.id,
            query_runs.STATUS_REJECTED,
            error_code=decision.error_code,
            error_message=decision.error_message,
        )
        return _respond_without_result(
            run, query_runs.STATUS_REJECTED, decision.error_code, decision.error_message
        )

    assert decision.normalized_sql is not None
    try:
        result = executor.execute_readonly(
            analytics_reader_engine(),
            decision.normalized_sql,
            timeout_ms=settings.query_timeout_ms,
            row_limit=settings.query_row_limit,
        )
    except executor.ExecutionError as error:
        message = query_runs.summarize_error(str(error))
        query_runs.finish_run(
            platform,
            run.id,
            query_runs.STATUS_FAILED,
            error_code=error.error_code,
            error_message=message,
            duration_ms=error.duration_ms,
        )
        return _respond_without_result(
            run,
            query_runs.STATUS_FAILED,
            error.error_code,
            message,
            duration_ms=error.duration_ms,
        )

    query_runs.finish_run(
        platform,
        run.id,
        query_runs.STATUS_SUCCEEDED,
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
    )
    return JSONResponse(
        status_code=201,
        content={
            "query_run": {
                "id": str(run.id),
                "status": query_runs.STATUS_SUCCEEDED,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "duration_ms": result.duration_ms,
                "error": None,
                "created_at": run.created_at.isoformat(),
            },
            "result": {"columns": result.columns, "rows": result.rows},
        },
    )


def _respond_without_result(
    run: query_runs.QueryRun,
    status: str,
    code: str,
    message: str,
    *,
    duration_ms: int | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=201,
        content={
            "query_run": {
                "id": str(run.id),
                "status": status,
                "row_count": None,
                "truncated": False,
                "duration_ms": duration_ms,
                "error": {"code": code, "message": message},
                "created_at": run.created_at.isoformat(),
            },
            "result": None,
        },
    )


@app.get("/api/v1/query-runs/{run_id}")
def read_query_run(run_id: str) -> Any:
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        return _error_response(404, "not_found", "查询记录不存在")
    run = query_runs.get_run(platform_engine(), parsed)
    if run is None:
        return _error_response(404, "not_found", "查询记录不存在")
    return {"query_run": query_runs.run_to_public_dict(run, include_sql=True)}
