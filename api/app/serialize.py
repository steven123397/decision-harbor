"""Convert ORM run + cached result into the API response envelope."""
from __future__ import annotations

from datetime import datetime

from .executor import ExecResult
from .models import QueryRun


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def to_response(run: QueryRun, result: ExecResult | None) -> dict:
    error = None
    if run.status == "rejected":
        error = {"code": run.rejection_code, "message": run.rejection_message}
    elif run.status == "failed":
        error = {"code": run.error_code, "message": run.error_summary}

    result_payload = None
    if result is not None:
        result_payload = {
            "columns": [{"name": c.name, "type": c.type} for c in result.columns],
            "rows": result.rows,
        }

    return {
        "id": run.id,
        "sql": run.sql_text,
        "status": run.status,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "row_count": run.row_count,
        "duration_ms": run.duration_ms,
        "error": error,
        "result": result_payload,
    }
