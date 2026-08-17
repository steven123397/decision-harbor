"""应用组装与生命周期：先引导（迁移 + seed），再启动服务。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.db import create_platform_engine, create_platform_session_factory
from app.readiness import Readiness
from app.routes.query_runs import InvalidQueryParam
from app.routes.query_runs import (
    invalid_request_handler as invalid_request,
)
from app.routes.query_runs import router as query_runs_router
from app.runs.service import QueryRunService
from app.seed.loader import contract_tables, ensure_dataset, load_facts

logger = logging.getLogger("decision_harbor")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    dataset_dir = Path(settings.dataset_dir)

    platform_engine = create_platform_engine(settings.platform_app_url)
    session_factory = create_platform_session_factory(platform_engine)
    facts = load_facts(dataset_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        readiness = Readiness(
            platform_engine, settings.analytics_readonly_dsn, facts.marker_key, facts.tables
        )
        app.state.readiness = readiness
        # API 进程只受理与读状态，不执行用户 SQL（ADR-0016）。
        app.state.runs = QueryRunService(
            session_factory,
            allowed_tables=contract_tables(dataset_dir),
            sql_max_length=settings.query_sql_max_length,
        )
        yield
        platform_engine.dispose()

    app = FastAPI(title="DecisionHarbor API", lifespan=lifespan)
    app.include_router(query_runs_router)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(InvalidQueryParam, invalid_query_param_handler)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        ok, reason = app.state.readiness.check()
        if not ok:
            return JSONResponse(status_code=503, content={"status": "unavailable", "reason": reason})
        return {"status": "ready"}

    return app


def invalid_query_param_handler(_: Request, exc: InvalidQueryParam) -> JSONResponse:
    """查询参数非法与请求体非法共用 {"error": ...} 400 信封（ADR-0018）。"""
    return JSONResponse(status_code=400, content={"error": exc.detail})


app = create_app()
