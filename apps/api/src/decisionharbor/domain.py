from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TypeAlias
from uuid import uuid4


JsonCell: TypeAlias = None | bool | int | float | str


@dataclass(frozen=True)
class QueryColumn:
    name: str
    type: str


@dataclass(frozen=True)
class ResultSnapshot:
    run_id: str
    payload: str
    truncated: bool
    row_count: int
    byte_size: int
    created_at: datetime


@dataclass(frozen=True)
class IdempotencyRecord:
    """提交幂等键的持久化占用：键作用域内与请求指纹绑定到唯一查询运行。"""

    scope: str
    key: str
    request_fingerprint: str
    run_id: str


@dataclass(frozen=True)
class QueryRun:
    id: str
    raw_sql: str
    status: str
    policy_decision: str
    policy_version: str
    referenced_objects: tuple[str, ...]
    statement_timeout_ms: int
    max_rows: int
    returned_row_count: int | None
    result_truncated: bool | None
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    retry_of: str | None = None

    @classmethod
    def received(
        cls,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        retry_of: str | None = None,
    ) -> "QueryRun":
        return cls(
            id=str(uuid4()),
            raw_sql=raw_sql,
            status="received",
            policy_decision="not_evaluated",
            policy_version=policy_version,
            referenced_objects=(),
            statement_timeout_ms=statement_timeout_ms,
            max_rows=max_rows,
            returned_row_count=None,
            result_truncated=None,
            error_code=None,
            error_summary=None,
            created_at=datetime.now(timezone.utc),
            started_at=None,
            finished_at=None,
            duration_ms=None,
            retry_of=retry_of,
        )


def finished_fields(run: QueryRun) -> dict[str, datetime | int]:
    finished_at = datetime.now(timezone.utc)
    duration_ms = max(0, int((finished_at - run.created_at).total_seconds() * 1_000))
    return {"finished_at": finished_at, "duration_ms": duration_ms}
