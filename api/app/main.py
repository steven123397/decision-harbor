"""FastAPI 应用：受治理 SQL 查询链路的最小 HTTP 接口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .audit import AuditRepo
from .config import get_settings
from .db import analytics_engine, platform_engine
from .executor import execute
from .policy import analyze
from .schemas import QueryRunCreate


def _truncate(text_: str, limit: int = 300) -> str:
    return text_ if len(text_) <= limit else text_[:limit]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.platform_engine = platform_engine()
    app.state.analytics_engine = analytics_engine()
    yield
    app.state.platform_engine.dispose()
    app.state.analytics_engine.dispose()


app = FastAPI(title="DecisionHarbor API", version="0.1.0", lifespan=lifespan)


def _http_status(run_status: str, error_code: str | None) -> int:
    if run_status == "succeeded":
        return 200
    if run_status == "rejected":
        return 422
    if run_status == "failed":
        return 500 if error_code == "INTERNAL_ERROR" else 200
    return 200


def _envelope(run: dict, columns=None, rows=None) -> dict:
    return {
        "id": run["id"],
        "status": run["status"],
        "sql": run["sql"],
        "error_code": run["error_code"],
        "error_message": run["error_message"],
        "columns": columns,
        "rows": rows,
        "row_count": run["row_count"],
        "duration_ms": run["duration_ms"],
        "created_at": run["created_at"],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready(request: Request):
    try:
        with request.app.state.platform_engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM query_runs LIMIT 1"))
        with request.app.state.analytics_engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM analytics.customers LIMIT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})


@app.post("/api/v1/query-runs")
def create_query_run(body: QueryRunCreate, request: Request):
    settings = request.app.state.settings
    audit = AuditRepo(request.app.state.platform_engine)

    try:
        run_id = audit.create(body.sql)
    except Exception as err:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "error_code": "INTERNAL_ERROR",
                "error_message": _truncate(str(err)),
                "sql": body.sql,
            },
        )

    try:
        policy = analyze(body.sql)
        if not policy.allowed:
            code = policy.violations[0]
            audit.mark_rejected(run_id, code, "; ".join(policy.violations))
            return JSONResponse(
                status_code=_http_status("rejected", code),
                content=_envelope(audit.get(run_id)),
            )

        audit.mark_running(run_id)
        exec_result = execute(
            body.sql,
            request.app.state.analytics_engine,
            settings.statement_timeout_ms,
            settings.row_limit,
        )
        if exec_result.succeeded:
            audit.mark_succeeded(run_id, exec_result.row_count, exec_result.duration_ms)
        else:
            audit.mark_failed(
                run_id,
                exec_result.error_code,
                exec_result.error_message,
                exec_result.duration_ms,
            )
        run = audit.get(run_id)
        content = _envelope(
            run,
            columns=exec_result.columns if exec_result.succeeded else None,
            rows=exec_result.rows if exec_result.succeeded else None,
        )
        return JSONResponse(
            status_code=_http_status(run["status"], run["error_code"]),
            content=content,
        )
    except Exception as err:  # noqa: BLE001
        try:
            audit.mark_failed(run_id, "INTERNAL_ERROR", _truncate(str(err)))
            return JSONResponse(
                status_code=500, content=_envelope(audit.get(run_id))
            )
        except Exception as err2:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={
                    "id": run_id,
                    "status": "failed",
                    "error_code": "INTERNAL_ERROR",
                    "error_message": _truncate(str(err2)),
                    "sql": body.sql,
                },
            )


@app.get("/api/v1/query-runs/{run_id}")
def get_query_run(run_id: int, request: Request):
    run = AuditRepo(request.app.state.platform_engine).get(run_id)
    if run is None:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "NOT_FOUND",
                "error_message": f"query run {run_id} not found",
            },
        )
    return JSONResponse(status_code=200, content=_envelope(run))
