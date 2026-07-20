"""FastAPI application entrypoint."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Generator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Database, get_db
from app.executor import QueryExecutor
from app.schemas import ApiEnvelope, ErrorBody, QueryRunCreate
from app.service import QueryRunService


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    database = database or get_db()

    app = FastAPI(title="DecisionHarbor API", version="0.1.0")
    app.state.settings = settings
    app.state.database = database

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        ok, detail = _check_ready(database, settings)
        if ok:
            return JSONResponse(
                status_code=200,
                content={"status": "ready", "detail": detail},
            )
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "detail": detail},
        )

    @app.post("/api/v1/query-runs")
    def create_query_run(body: QueryRunCreate) -> JSONResponse:
        with _platform_session(database) as session:
            service = _service(session, database, settings)
            result = service.submit(body.sql)
            run = result.run
            if result.http_related_status == "succeeded":
                envelope = ApiEnvelope(
                    status="succeeded",
                    id=run.id,
                    data={
                        "columns": result.columns,
                        "rows": result.rows,
                        "row_count": run.row_count,
                        "duration_ms": run.duration_ms,
                    },
                    error=None,
                )
            else:
                envelope = ApiEnvelope(
                    status=result.http_related_status,
                    id=run.id,
                    data={
                        "row_count": run.row_count,
                        "duration_ms": run.duration_ms,
                    },
                    error=ErrorBody(
                        code=run.error_code or "UNKNOWN",
                        message=run.error_message or "Unknown error",
                    ),
                )
            return JSONResponse(status_code=200, content=_dump(envelope))

    @app.get("/api/v1/query-runs/{run_id}")
    def get_query_run(run_id: uuid.UUID) -> JSONResponse:
        with _platform_session(database) as session:
            service = _service(session, database, settings)
            run = service.get(run_id)
            if run is None:
                return JSONResponse(
                    status_code=404,
                    content=_dump(
                        ApiEnvelope(
                            status="failed",
                            id=run_id,
                            data=None,
                            error=ErrorBody(
                                code="NOT_FOUND",
                                message="Query run not found",
                            ),
                        )
                    ),
                )
            error = None
            if run.error_code:
                error = ErrorBody(
                    code=run.error_code,
                    message=run.error_message or "",
                )
            envelope = ApiEnvelope(
                status=run.status,
                id=run.id,
                data={
                    "sql_text": run.sql_text,
                    "status": run.status,
                    "row_count": run.row_count,
                    "duration_ms": run.duration_ms,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "finished_at": run.finished_at.isoformat()
                    if run.finished_at
                    else None,
                    "error": error.model_dump() if error else None,
                },
                error=error if run.status in {"rejected", "failed"} else None,
            )
            return JSONResponse(status_code=200, content=_dump(envelope))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_dump(
                ApiEnvelope(
                    status="failed",
                    id=None,
                    data=None,
                    error=ErrorBody(
                        code="REQUEST_INVALID",
                        message="Invalid request body",
                    ),
                )
            ),
        )

    return app


def _service(session: Session, database: Database, settings: Settings) -> QueryRunService:
    executor = QueryExecutor(
        database.analytics_readonly_engine,
        statement_timeout_ms=settings.statement_timeout_ms,
        max_result_rows=settings.max_result_rows,
    )
    return QueryRunService(session, executor)


@contextmanager
def _platform_session(database: Database) -> Generator[Session, None, None]:
    session = database.PlatformSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _check_ready(database: Database, settings: Settings) -> tuple[bool, dict[str, Any]]:
    detail: dict[str, Any] = {}
    try:
        with database.platform_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            exists = conn.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'query_runs')"
                )
            ).scalar()
            detail["platform_query_runs"] = bool(exists)
            if not exists:
                return False, detail
    except Exception as exc:  # noqa: BLE001
        detail["platform_error"] = str(exc)
        return False, detail

    try:
        with database.analytics_readonly_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            # Prefer seed meta; fall back to row count of customers.
            meta = conn.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'analytics_seed_meta')"
                )
            ).scalar()
            if meta:
                row = conn.execute(
                    text(
                        "SELECT dataset, version FROM analytics_seed_meta "
                        "ORDER BY applied_at DESC LIMIT 1"
                    )
                ).first()
                detail["seed"] = {"dataset": row[0], "version": row[1]} if row else None
                if row is None:
                    return False, detail
            else:
                count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()
                detail["customers_count"] = count
                if count != 100:
                    return False, detail
    except Exception as exc:  # noqa: BLE001
        detail["analytics_error"] = str(exc)
        return False, detail

    return True, detail


def _dump(envelope: ApiEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json")


app = create_app()
