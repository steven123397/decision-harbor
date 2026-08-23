from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
from threading import Lock
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from decisionharbor.cleanup import is_expired
from decisionharbor.config import Settings
from decisionharbor.dataset import load_dataset
from decisionharbor.domain import QueryRun, ResultSnapshot
from decisionharbor.policy import SqlPolicy
from decisionharbor.readiness import AnalyticsReadinessProbe, PlatformReadinessProbe
from decisionharbor.repository import (
    CANCEL_OUTCOME_CANCELLED,
    CANCEL_OUTCOME_TERMINAL,
    QueryRunRepository,
)
from decisionharbor.service import QueryRunService, ServiceFailure


MAX_REQUEST_BYTES = 128 * 1024
READINESS_TIMEOUT_SECONDS = 1.0
IDEMPOTENCY_KEY_MAX_CHARS = 128

TERMINAL_STATUSES = frozenset({"rejected", "succeeded", "failed", "cancelled"})

HTTP_STATUS_BY_CODE = {
    "invalid_request": 422,
    "invalid_idempotency_key": 422,
    "sql_empty": 422,
    "sql_too_large": 422,
    "sql_parse_error": 422,
    "multiple_statements": 422,
    "sql_statement_not_allowed": 422,
    "sql_object_not_allowed": 422,
    "sql_function_not_allowed": 422,
    "unsupported_sql": 422,
    "query_run_not_found": 404,
    "query_semantic_error": 400,
    "result_not_ready": 409,
    "result_unavailable": 409,
    "result_expired": 410,
    "idempotency_conflict": 409,
    "query_run_not_cancellable": 409,
    "query_capacity_exceeded": 429,
    "query_timeout": 504,
    "analytics_unavailable": 503,
    "audit_unavailable": 503,
    "service_not_ready": 503,
    "policy_internal_error": 500,
    "internal_error": 500,
    "unsupported_result_type": 500,
}


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str


class QueryService(Protocol):
    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun: ...

    def cancel(self, run_id: str) -> tuple[str, QueryRun]: ...


class QueryRepository(Protocol):
    def get(self, run_id: str) -> QueryRun | None: ...

    def get_result_snapshot(self, run_id: str) -> ResultSnapshot | None: ...


def _envelope(data: object = None, error: object = None) -> dict[str, object]:
    return {"data": data, "error": error}


def _run_payload(run: object) -> dict:
    return jsonable_encoder(asdict(run))


def _error(code: str, message: str, query_run_id: str | None = None) -> dict[str, str]:
    payload = {"code": code, "message": message}
    if query_run_id:
        payload["query_run_id"] = query_run_id
    return payload


def _idempotency_key_error(key: str) -> str | None:
    """键为 1 到 128 个可见 ASCII 字符；非法时返回错误消息。"""
    if not 1 <= len(key) <= IDEMPOTENCY_KEY_MAX_CHARS:
        return "The idempotency key must be 1 to 128 characters."
    if not all(33 <= ord(character) <= 126 for character in key):
        return "The idempotency key must contain only visible ASCII characters."
    return None


def _failure_response(failure: ServiceFailure, *, include_run: bool) -> JSONResponse:
    """统一 ServiceFailure 映射：稳定错误码 + 脱敏消息 + 可选的运行事实。"""
    run_id = failure.query_run.id if failure.query_run else None
    data = {"query_run": _run_payload(failure.query_run)} if include_run and failure.query_run else None
    return JSONResponse(
        _envelope(data=data, error=_error(failure.code, failure.message, run_id)),
        status_code=HTTP_STATUS_BY_CODE.get(failure.code, 500),
    )


def create_app(
    *,
    service: QueryService,
    repository: QueryRepository,
    readiness_check: Callable[[], bool],
) -> FastAPI:
    readiness_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readiness")
    readiness_lock = Lock()
    readiness_future: Future[bool] | None = None

    def check_readiness_with_deadline() -> bool:
        nonlocal readiness_future
        with readiness_lock:
            if readiness_future is not None and not readiness_future.done():
                return False
            readiness_future = readiness_executor.submit(readiness_check)
        try:
            return bool(readiness_future.result(timeout=READINESS_TIMEOUT_SECONDS))
        except TimeoutError:
            return False
        except Exception:
            return False

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            readiness_executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="DecisionHarbor API", version="1.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def enforce_body_limit(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/v1/query-runs":
            content_length = request.headers.get("content-length")
            try:
                body_too_large = bool(content_length and int(content_length) > MAX_REQUEST_BYTES)
            except ValueError:
                body_too_large = True
            if body_too_large:
                return JSONResponse(
                    _envelope(error=_error("invalid_request", "The request body is invalid.")),
                    status_code=422,
                )
            body = await request.body()
            if len(body) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    _envelope(error=_error("invalid_request", "The request body is invalid.")),
                    status_code=422,
                )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            _envelope(error=_error("invalid_request", "The request body is invalid.")),
            status_code=422,
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        return _envelope(data={"status": "ok"})

    @app.get("/ready", response_model=None)
    def ready():
        if not check_readiness_with_deadline():
            return JSONResponse(
                _envelope(error=_error("service_not_ready", "The service is not ready.")),
                status_code=503,
            )
        return _envelope(data={"status": "ready"})

    @app.post("/api/v1/query-runs", response_model=None)
    def create_query_run(query: QueryRequest, request: Request):
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key is not None:
            invalid_reason = _idempotency_key_error(idempotency_key)
            if invalid_reason is not None:
                return JSONResponse(
                    _envelope(error=_error("invalid_idempotency_key", invalid_reason)),
                    status_code=422,
                )
        try:
            run = service.submit(query.sql, idempotency_key=idempotency_key)
        except ServiceFailure as failure:
            # 策略拒绝等失败仍随响应返回原运行事实（rejected 终态可读）。
            return _failure_response(failure, include_run=True)
        return JSONResponse(
            _envelope(data={"query_run": _run_payload(run)}),
            status_code=202,
        )

    @app.get("/api/v1/query-runs/{run_id}", response_model=None)
    def get_query_run(run_id: UUID):
        try:
            run = repository.get(str(run_id))
        except Exception:
            return JSONResponse(
                _envelope(error=_error("audit_unavailable", "The audit store is unavailable.")),
                status_code=503,
            )
        if run is None:
            return JSONResponse(
                _envelope(error=_error("query_run_not_found", "Query run was not found.")),
                status_code=404,
            )
        return _envelope(data={"query_run": _run_payload(run)})

    @app.get("/api/v1/query-runs/{run_id}/result", response_model=None)
    def get_query_run_result(run_id: UUID):
        try:
            run = repository.get(str(run_id))
            snapshot = repository.get_result_snapshot(str(run_id)) if run is not None else None
        except Exception:
            return JSONResponse(
                _envelope(error=_error("audit_unavailable", "The audit store is unavailable.")),
                status_code=503,
            )
        if run is None:
            return JSONResponse(
                _envelope(error=_error("query_run_not_found", "Query run was not found.")),
                status_code=404,
            )
        if run.status not in TERMINAL_STATUSES:
            return JSONResponse(
                _envelope(
                    error=_error(
                        "result_not_ready",
                        "The query result is not ready yet.",
                        run.id,
                    )
                ),
                status_code=409,
            )
        if run.status != "succeeded":
            return JSONResponse(
                _envelope(
                    error=_error(
                        "result_unavailable",
                        "The query result is not available for this run.",
                        run.id,
                    )
                ),
                status_code=409,
            )
        if run.finished_at is None or is_expired(run.finished_at):
            # 已清理或未清理的过期快照统一明确过期；清理只删结果内容，
            # succeeded 运行事实本身证明结果曾成功发布。
            return JSONResponse(
                _envelope(
                    error=_error(
                        "result_expired",
                        "The query result has expired.",
                        run.id,
                    )
                ),
                status_code=410,
            )
        if snapshot is None:
            return JSONResponse(
                _envelope(
                    error=_error(
                        "result_unavailable",
                        "The query result is not available for this run.",
                        run.id,
                    )
                ),
                status_code=409,
            )
        result = {**json.loads(snapshot.payload), "truncated": snapshot.truncated}
        return _envelope(data={"result": result})

    @app.post("/api/v1/query-runs/{run_id}/cancel", response_model=None)
    def cancel_query_run(run_id: UUID):
        try:
            outcome, run = service.cancel(str(run_id))
        except ServiceFailure as failure:
            return _failure_response(failure, include_run=False)
        # queued 直接取消与既有终态返回 200；取消意图已记录（含重复取消）返回 202。
        status_code = 200 if outcome in (CANCEL_OUTCOME_CANCELLED, CANCEL_OUTCOME_TERMINAL) else 202
        return JSONResponse(
            _envelope(data={"query_run": _run_payload(run)}),
            status_code=status_code,
        )

    return app


def create_runtime_app() -> FastAPI:
    settings = Settings.from_env()
    dataset = load_dataset(settings.dataset_root)
    repository = QueryRunRepository(settings.platform_database_url)
    platform_readiness = PlatformReadinessProbe(settings.platform_database_url)
    analytics_readiness = AnalyticsReadinessProbe(settings.analytics_readiness_database_url)
    policy = SqlPolicy(dataset.allowed_tables)
    service = QueryRunService(
        repository=repository,
        policy=policy,
        policy_version=dataset.policy_version,
        statement_timeout_ms=settings.statement_timeout_ms,
        max_rows=settings.max_rows,
    )
    return create_app(
        service=service,
        repository=repository,
        readiness_check=lambda: platform_readiness.check_ready() and analytics_readiness.check_ready(dataset),
    )
