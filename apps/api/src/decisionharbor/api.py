from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from threading import Lock
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from decisionharbor.config import Settings
from decisionharbor.dataset import load_dataset
from decisionharbor.domain import QueryResult, QueryRun
from decisionharbor.policy import SqlPolicy
from decisionharbor.readiness import AnalyticsReadinessProbe, PlatformReadinessProbe
from decisionharbor.repository import QueryRunRepository
from decisionharbor.service import QueryRunService, ServiceFailure


MAX_REQUEST_BYTES = 128 * 1024
READINESS_TIMEOUT_SECONDS = 1.0

HTTP_STATUS_BY_CODE = {
    "invalid_request": 422,
    "sql_empty": 422,
    "sql_too_large": 422,
    "sql_parse_error": 422,
    "multiple_statements": 422,
    "sql_statement_not_allowed": 422,
    "sql_object_not_allowed": 422,
    "sql_function_not_allowed": 422,
    "unsupported_sql": 422,
    "invalid_idempotency_key": 422,
    "idempotency_conflict": 409,
    "query_run_not_found": 404,
    "result_not_ready": 409,
    "result_unavailable": 409,
    "result_expired": 410,
    "audit_unavailable": 503,
    "service_not_ready": 503,
    "policy_internal_error": 500,
    "internal_error": 500,
}


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sql: str


class QueryService(Protocol):
    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun: ...

    def read_result(self, run_id: str) -> QueryResult: ...


class QueryRepository(Protocol):
    def get(self, run_id: str): ...

    def recover_interrupted(self) -> int: ...


def _envelope(data: object = None, error: object = None) -> dict[str, object]:
    return {"data": data, "error": error}


def _run_payload(run: object) -> dict:
    return jsonable_encoder(asdict(run))


def _error(code: str, message: str, query_run_id: str | None = None) -> dict[str, str]:
    payload = {"code": code, "message": message}
    if query_run_id:
        payload["query_run_id"] = query_run_id
    return payload


def _failure_response(failure: ServiceFailure) -> JSONResponse:
    """A service failure as a stable code, its HTTP status and the envelope."""
    run = failure.query_run
    return JSONResponse(
        _envelope(
            data={"query_run": _run_payload(run)} if run else None,
            error=_error(failure.code, failure.message, run.id if run else None),
        ),
        status_code=HTTP_STATUS_BY_CODE.get(failure.code, 500),
    )


def create_app(
    *,
    service: QueryService,
    repository: QueryRepository,
    readiness_check: Callable[[], bool],
    recover_on_startup: bool = True,
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
            if recover_on_startup:
                repository.recover_interrupted()
            yield
        finally:
            readiness_executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="DecisionHarbor API", version="1.0.0", lifespan=lifespan)

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
    def create_query_run(
        query: QueryRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        try:
            run = service.submit(query.sql, idempotency_key)
        except ServiceFailure as failure:
            return _failure_response(failure)
        return JSONResponse(
            _envelope(data={"query_run": _run_payload(run)}),
            status_code=202,
        )

    @app.get("/api/v1/query-runs/{run_id}", response_model=None)
    def get_query_run(run_id: UUID):
        try:
            run = repository.get(str(run_id))
        except Exception:
            return _failure_response(
                ServiceFailure("audit_unavailable", "The audit store is unavailable.", None)
            )
        if run is None:
            return _failure_response(
                ServiceFailure("query_run_not_found", "Query run was not found.", None)
            )
        return _envelope(data={"query_run": _run_payload(run)})

    @app.get("/api/v1/query-runs/{run_id}/result", response_model=None)
    def get_query_run_result(run_id: UUID):
        try:
            result = service.read_result(str(run_id))
        except ServiceFailure as failure:
            return _failure_response(failure)
        return _envelope(data={"result": jsonable_encoder(asdict(result))})

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
