"""API 请求/响应模型。字段对应 docs/design/api-and-workbench.md 的统一信封。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models import QueryRun


class QueryRequest(BaseModel):
    sql: str


class ColumnDef(BaseModel):
    name: str
    type: str


class ResultOut(BaseModel):
    columns: list[ColumnDef]
    rows: list[list[Any]]
    truncated: bool


class RecordOut(BaseModel):
    id: uuid.UUID
    status: str
    sql: str
    error_code: str | None
    error_message: str | None
    row_count: int | None
    duration_ms: int | None
    created_at: datetime

    @classmethod
    def from_run(cls, run: QueryRun) -> "RecordOut":
        return cls(
            id=run.id,
            status=run.status,
            sql=run.sql_text,
            error_code=run.error_code,
            error_message=run.error_message,
            row_count=run.row_count,
            duration_ms=run.duration_ms,
            created_at=run.created_at,
        )


class QueryRunResponse(BaseModel):
    record: RecordOut
    result: ResultOut | None


class RecordResponse(BaseModel):
    record: RecordOut
