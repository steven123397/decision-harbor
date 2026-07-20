"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class QueryRunCreate(BaseModel):
    sql: str = Field(min_length=1)


class ErrorBody(BaseModel):
    code: str
    message: str


class ApiEnvelope(BaseModel):
    status: str
    id: UUID | None = None
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None
