"""查询审计仓储：在 platform 库读写 query_runs，仅保存事实，不保存结果行。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import QueryRun


class AuditRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, sql_text: str) -> int:
        with Session(self._engine) as session:
            run = QueryRun(sql_text=sql_text, status="pending")
            session.add(run)
            session.commit()
            session.refresh(run)
            return run.id

    def mark_running(self, run_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(QueryRun).where(QueryRun.id == run_id).values(status="running")
            )

    def mark_rejected(self, run_id: int, code: str, message: str) -> None:
        self._mark_terminal(run_id, "rejected", error_code=code, error_message=message)

    def mark_succeeded(self, run_id: int, row_count: int, duration_ms: int) -> None:
        self._mark_terminal(
            run_id, "succeeded", row_count=row_count, duration_ms=duration_ms
        )

    def mark_failed(
        self,
        run_id: int,
        code: str,
        message: str,
        duration_ms: int | None = None,
    ) -> None:
        self._mark_terminal(
            run_id,
            "failed",
            error_code=code,
            error_message=message,
            duration_ms=duration_ms,
        )

    def _mark_terminal(self, run_id: int, status: str, **values) -> None:
        values["finished_at"] = datetime.now(timezone.utc)
        with self._engine.begin() as conn:
            conn.execute(
                update(QueryRun).where(QueryRun.id == run_id).values(status=status, **values)
            )

    def get(self, run_id: int) -> dict | None:
        with Session(self._engine) as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            return {
                "id": run.id,
                "sql": run.sql_text,
                "status": run.status,
                "error_code": run.error_code,
                "error_message": run.error_message,
                "row_count": run.row_count,
                "duration_ms": run.duration_ms,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
