from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .audit import SqlAuditStore
from .domain import AuditPersistenceError, QueryResult, QueryRunResponse
from .executor import SqlAlchemyAnalyticsExecutor
from .policy import evaluate_sql
from .readiness import ReadinessChecker
from .service import QueryRunService
from .settings import Settings


HTTP_MAX_BODY_BYTES = 128 * 1024


class QueryRunRequest(BaseModel):
    sql: str


def create_app(
    service: QueryRunService | None = None,
    readiness: ReadinessChecker | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    owned: list[Any] = []
    if service is None:
        audit = SqlAuditStore(settings.platform_runtime_url)
        executor = SqlAlchemyAnalyticsExecutor(settings)
        service = QueryRunService(
            audit,
            executor,
            policy_evaluator=lambda sql: evaluate_sql(
                sql,
                max_sql_bytes=settings.max_sql_bytes,
                max_result_rows=settings.max_result_rows,
            ),
        )
        owned.extend([audit, executor])
    if readiness is None:
        readiness = ReadinessChecker(settings)
        owned.append(readiness)

    app = FastAPI(title="DecisionHarbor API", version="0.1.0")
    app.state.query_run_service = service
    app.state.readiness = readiness
    app.state.owned_resources = owned

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > HTTP_MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content=_error_body("request_too_large", "Request body is too large"))
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        return JSONResponse(status_code=400, content=_error_body("invalid_request", "Request body is invalid"))

    @app.exception_handler(AuditPersistenceError)
    async def audit_error(_request: Request, _error: AuditPersistenceError):
        return JSONResponse(status_code=503, content=_error_body("service_not_ready", "Query audit storage is unavailable"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        if not app.state.readiness.check():
            return JSONResponse(status_code=503, content=_error_body("service_not_ready", "Database migrations are not ready"))
        return {"status": "ready"}

    @app.post("/api/v1/query-runs")
    def submit(request: QueryRunRequest) -> dict[str, Any]:
        response = app.state.query_run_service.submit(request.sql)
        return _response_body(response, include_result=True)

    @app.get("/api/v1/query-runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        response = app.state.query_run_service.get(run_id)
        if response is None:
            return JSONResponse(status_code=404, content=_error_body("query_run_not_found", "Query run was not found"))
        return _response_body(response, include_result=False)

    return app


def _response_body(response: QueryRunResponse, include_result: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": response.run_id,
        "raw_sql": response.raw_sql,
        "state": response.state.value,
        "outcome": response.outcome.value if isinstance(response.outcome, Enum) else response.outcome,
        "created_at": _datetime(response.created_at),
        "policy": {"decision": response.policy_decision, "code": response.policy_code},
        "row_count": response.row_count,
        "duration_ms": response.duration_ms,
        "result": _result_body(response.result) if include_result and response.result else None,
        "error": (
            {"code": response.error_code, "message": response.error_message}
            if response.error_code
            else None
        ),
    }
    return body


def _result_body(result: QueryResult) -> dict[str, Any]:
    return {
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
    }


def _datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}
