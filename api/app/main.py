"""FastAPI 入口：路由、统一错误信封、/health 与 /ready。"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import psycopg_url
from app.errors import INVALID_REQUEST, ApiError
from app.policy import DEFAULT_ALLOWED_TABLES
from app.routers import query_runs

app = FastAPI(title="DecisionHarbor API")
app.include_router(query_runs.router)


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": INVALID_REQUEST,
                "message": '请求体不合法：需要 { "sql": "..." }。',
            }
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _ready_check() -> bool:
    """双库可连接且迁移已建立（审计表与契约五表存在）。"""
    settings = get_settings()
    try:
        with psycopg.connect(
            psycopg_url(settings.platform_url), connect_timeout=2
        ) as conn:
            row = conn.execute("SELECT to_regclass('public.query_runs')").fetchone()
            if row is None or row[0] is None:
                return False
        with psycopg.connect(
            psycopg_url(settings.analytics_readonly_url), connect_timeout=2
        ) as conn:
            for table in sorted(DEFAULT_ALLOWED_TABLES):
                row = conn.execute(
                    "SELECT to_regclass(%s)", (f"analytics.{table}",)
                ).fetchone()
                if row is None or row[0] is None:
                    return False
    except (psycopg.Error, RuntimeError):
        return False
    return True


@app.get("/ready")
def ready() -> JSONResponse:
    if _ready_check():
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(status_code=503, content={"status": "not_ready"})
