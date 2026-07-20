"""Platform query_runs repository."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import QueryRun


class QueryRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_running(self, sql_text: str) -> QueryRun:
        row = QueryRun(sql_text=sql_text, status="running")
        self._session.add(row)
        self._session.flush()
        return row

    def finalize(
        self,
        row: QueryRun,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        row_count: int | None = None,
        duration_ms: int | None = None,
    ) -> QueryRun:
        row.status = status
        row.error_code = error_code
        row.error_message = error_message
        row.row_count = row_count
        row.duration_ms = duration_ms
        row.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return row

    def get(self, run_id: uuid.UUID) -> QueryRun | None:
        return self._session.get(QueryRun, run_id)
