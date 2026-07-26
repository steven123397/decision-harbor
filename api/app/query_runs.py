"""查询记录（query run）：审计事实的建立与状态更新，只写 platform 库。

表结构与状态机见 docs/design/query-runs-api.md；终态不可再变更。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    insert,
    select,
    update,
)

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_REJECTED, STATUS_FAILED})

ERROR_MESSAGE_LIMIT = 500

metadata = MetaData()

query_runs_table = Table(
    "query_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("sql_text", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("error_code", String(50), nullable=True),
    Column("error_message", Text, nullable=True),
    Column("row_count", Integer, nullable=True),
    Column("truncated", Boolean, nullable=False, server_default="false"),
    Column("duration_ms", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def summarize_error(message: str) -> str:
    """错误摘要：取首行并截断，见设计中审计表 error_message 的口径。"""
    first_line = message.strip().splitlines()[0] if message.strip() else "unknown error"
    return first_line[:ERROR_MESSAGE_LIMIT]


@dataclass(frozen=True)
class QueryRun:
    id: uuid.UUID
    sql_text: str
    status: str
    error_code: str | None
    error_message: str | None
    row_count: int | None
    truncated: bool
    duration_ms: int | None
    created_at: datetime


def create_run(engine: Engine, sql_text: str) -> QueryRun:
    run = QueryRun(
        id=uuid.uuid4(),
        sql_text=sql_text,
        status=STATUS_RUNNING,
        error_code=None,
        error_message=None,
        row_count=None,
        truncated=False,
        duration_ms=None,
        created_at=datetime.now(timezone.utc),
    )
    with engine.begin() as conn:
        conn.execute(
            insert(query_runs_table).values(
                id=run.id,
                sql_text=run.sql_text,
                status=run.status,
                truncated=run.truncated,
                created_at=run.created_at,
            )
        )
    return run


def finish_run(
    engine: Engine,
    run_id: uuid.UUID,
    status: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    row_count: int | None = None,
    truncated: bool = False,
    duration_ms: int | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"不是终态：{status}")
    with engine.begin() as conn:
        conn.execute(
            update(query_runs_table)
            .where(
                query_runs_table.c.id == run_id,
                query_runs_table.c.status == STATUS_RUNNING,
            )
            .values(
                status=status,
                error_code=error_code,
                error_message=error_message,
                row_count=row_count,
                truncated=truncated,
                duration_ms=duration_ms,
            )
        )


def get_run(engine: Engine, run_id: uuid.UUID) -> QueryRun | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(query_runs_table).where(query_runs_table.c.id == run_id)
        ).mappings().first()
    if row is None:
        return None
    return QueryRun(**dict(row))


def run_to_public_dict(run: QueryRun, *, include_sql: bool = False) -> dict[str, Any]:
    """组装对外响应中的 query_run 对象，见 docs/design/query-runs-api.md。"""
    payload: dict[str, Any] = {
        "id": str(run.id),
        "status": run.status,
        "row_count": run.row_count,
        "truncated": run.truncated,
        "duration_ms": run.duration_ms,
        "error": (
            {"code": run.error_code, "message": run.error_message}
            if run.error_code
            else None
        ),
        "created_at": run.created_at.isoformat(),
    }
    if include_sql:
        payload["sql_text"] = run.sql_text
    return payload
