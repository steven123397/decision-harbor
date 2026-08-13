from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


QueryStatus = Literal["running", "succeeded", "rejected", "failed"]


class QueryRequest(BaseModel):
    sql: str = Field(min_length=0)


class ErrorBody(BaseModel):
    code: str
    message: str


class ResultColumn(BaseModel):
    name: str
    type: str


class QueryResult(BaseModel):
    columns: list[ResultColumn]
    rows: list[list[Any]]
    row_count: int


class QueryRunResponse(BaseModel):
    id: UUID
    status: QueryStatus
    sql: str
    result: QueryResult | None
    error: ErrorBody | None
    duration_ms: int | None
    row_count: int | None
    created_at: datetime
