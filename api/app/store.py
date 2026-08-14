"""Query run audit persistence against the platform database."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import QueryRun


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QueryRunStore:
    def __init__(self, dsn: str) -> None:
        self._engine = create_engine(dsn, pool_pre_ping=True, future=True)
        self._factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    def create_rejected(self, sql: str, code: str, message: str) -> QueryRun:
        run = QueryRun(
            sql_text=sql,
            policy_verdict="denied",
            status="rejected",
            rejection_code=code,
            rejection_message=message,
        )
        with self._factory() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        return run

    def create_running(self, sql: str) -> QueryRun:
        run = QueryRun(
            sql_text=sql,
            policy_verdict="allowed",
            status="running",
            started_at=utcnow(),
        )
        with self._factory() as session:
            session.add(run)
            session.commit()
            session.refresh(run)
        return run

    def set_succeeded(self, run_id: str, row_count: int, duration_ms: int) -> QueryRun | None:
        with self._factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            run.status = "succeeded"
            run.row_count = row_count
            run.duration_ms = duration_ms
            run.finished_at = utcnow()
            session.commit()
            session.refresh(run)
            return run

    def set_failed(self, run_id: str, code: str, summary: str, duration_ms: int | None = None) -> QueryRun | None:
        with self._factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            run.status = "failed"
            run.error_code = code
            run.error_summary = summary
            run.duration_ms = duration_ms
            run.finished_at = utcnow()
            session.commit()
            session.refresh(run)
            return run

    def get(self, run_id: str) -> QueryRun | None:
        with self._factory() as session:
            return session.get(QueryRun, run_id)

    def reconcile_stale_running(self) -> int:
        """Transition any lingering running rows to failed (EXEC_INTERRUPTED)."""
        with self._factory() as session:
            runs = session.scalars(select(QueryRun).where(QueryRun.status == "running")).all()
            for run in runs:
                run.status = "failed"
                run.error_code = "EXEC_INTERRUPTED"
                run.error_summary = "Interrupted by process restart"
                run.finished_at = utcnow()
            session.commit()
            return len(runs)
