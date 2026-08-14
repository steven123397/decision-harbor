"""Query run submission and status endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from ..config import settings
from ..container import service
from ..serialize import to_response

router = APIRouter()


@router.post("/api/v1/query-runs")
def submit(payload: dict[str, Any] = Body(...)):
    sql = payload.get("sql")
    if not isinstance(sql, str):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_REQUEST", "message": "body must contain a string 'sql' field"}},
        )
    if len(sql.encode("utf-8")) > settings.max_sql_bytes:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_REQUEST", "message": "sql exceeds the maximum allowed size"}},
        )
    run = service.submit(sql)
    body = to_response(run, None)
    if run.status == "rejected":
        return JSONResponse(status_code=422, content=body)
    return JSONResponse(status_code=202, content=body)


@router.get("/api/v1/query-runs/{run_id}")
def get_run(run_id: str):
    run, result = service.get(run_id)
    if run is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "query run not found"}},
        )
    return JSONResponse(status_code=200, content=to_response(run, result))
