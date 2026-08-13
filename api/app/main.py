from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from app.bootstrap import bootstrap
from app.config import Settings, load_settings
from app.db import Engines
from app.executor import QueryExecutor
from app.schemas import QueryRequest, QueryRunResponse
from app.service import QueryRunService, to_audit_response


API_DIR = Path(__file__).resolve().parents[1]
ready = False
settings: Settings | None = None
engines: Engines | None = None
query_service: QueryRunService | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ready, settings, engines, query_service
    settings = load_settings()
    if settings.skip_bootstrap or not (
        settings.platform_database_url
        and settings.analytics_owner_database_url
        and settings.analytics_reader_database_url
    ):
        ready = False
        query_service = None
        yield
        return
    engines = Engines(settings)
    bootstrap(settings, engines, API_DIR)
    factory = sessionmaker(bind=engines.platform, expire_on_commit=False)
    executor = QueryExecutor(
        engines.analytics_reader,
        timeout_ms=settings.query_statement_timeout_ms,
        max_rows=settings.query_max_rows,
    )
    query_service = QueryRunService(factory, executor, settings)
    ready = True
    try:
        yield
    finally:
        ready = False
        if engines is not None:
            engines.dispose()


app = FastAPI(title="DecisionHarbor API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_invalid(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "REQUEST_INVALID", "message": "Request body must be {\"sql\": string}."}},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def readiness() -> JSONResponse:
    if not ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(status_code=200, content={"status": "ready"})


@app.post("/api/v1/query-runs", response_model=QueryRunResponse)
def create_query_run(body: QueryRequest) -> QueryRunResponse:
    if query_service is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})  # type: ignore[return-value]
    return query_service.create(body.sql)


@app.get("/api/v1/query-runs/{run_id}", response_model=QueryRunResponse)
def get_query_run(run_id: str) -> QueryRunResponse | JSONResponse:
    try:
        parsed = UUID(run_id)
    except ValueError:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "QUERY_RUN_NOT_FOUND", "message": "Query run was not found."}},
        )
    if query_service is None:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    run = query_service.get(parsed)
    if run is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "QUERY_RUN_NOT_FOUND", "message": "Query run was not found."}},
        )
    return to_audit_response(run)
