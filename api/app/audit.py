"""审计仓储：查询运行记录的创建、终态更新与读取。结构对应 docs/design/query-governance.md。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import STATUS_RUNNING, QueryRun


def create_run(session: Session, sql_text: str) -> QueryRun:
    run = QueryRun(sql_text=sql_text, status=STATUS_RUNNING)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def finalize_run(
    session: Session,
    run: QueryRun,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    row_count: int | None = None,
    duration_ms: int | None = None,
) -> QueryRun:
    run.status = status
    run.error_code = error_code
    run.error_message = error_message
    run.row_count = row_count
    run.duration_ms = duration_ms
    session.commit()
    session.refresh(run)
    return run


def get_run(session: Session, run_id: uuid.UUID) -> QueryRun | None:
    return session.execute(
        select(QueryRun).where(QueryRun.id == run_id)
    ).scalar_one_or_none()
