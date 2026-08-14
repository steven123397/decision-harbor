from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class RunState(str, Enum):
    RECEIVED = "received"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[dict[str, str], ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    duration_ms: int


@dataclass(frozen=True)
class QueryRunResponse:
    run_id: str
    raw_sql: str | None
    state: RunState
    outcome: RunState | None
    created_at: datetime
    policy_decision: str
    policy_code: str | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    result: QueryResult | None = None
    error_code: str | None = None
    error_message: str | None = None


class ExecutionFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuditPersistenceError(Exception):
    pass
