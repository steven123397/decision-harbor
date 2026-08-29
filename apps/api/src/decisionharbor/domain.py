from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re
from typing import TypeAlias
from uuid import uuid4


JsonCell: TypeAlias = None | bool | int | float | str

SUBMIT_IDEMPOTENCY_SCOPE = "submit"
IDEMPOTENCY_KEY_MAX_LENGTH = 128
VISIBLE_ASCII_KEY = re.compile(rf"^[\x21-\x7e]{{1,{IDEMPOTENCY_KEY_MAX_LENGTH}}}$")


@dataclass(frozen=True)
class QueryColumn:
    name: str
    type: str


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[QueryColumn, ...]
    rows: tuple[tuple[JsonCell, ...], ...]
    truncated: bool


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
    cancellation_requested_at: datetime | None = None
    execution_attempt_count: int = 0
    attempt_number: int | None = None
    attempt_worker_id: str | None = None
    attempt_generation: int | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    retry_of: str | None = None

    @classmethod
    def received(
        cls,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
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
        )


@dataclass(frozen=True)
class IdempotencyClaim:
    """The idempotency scope, key and input fingerprint of one request."""

    scope: str
    key: str
    request_fingerprint: str


@dataclass(frozen=True)
class SubmitReservation:
    """A submit request that was reserved under its idempotency key.

    `is_replay` marks a reservation that reused a recorded key, so the caller
    replays the recorded query run instead of taking a new policy decision.
    `fingerprint_matched` only carries meaning for a replay: it says whether the
    replayed request carries the same input as the request that recorded the key.
    """

    run: QueryRun
    is_replay: bool = False
    fingerprint_matched: bool = True

    @classmethod
    def created(cls, run: QueryRun) -> "SubmitReservation":
        return cls(run=run)

    @classmethod
    def replayed(cls, run: QueryRun, fingerprint_matched: bool) -> "SubmitReservation":
        return cls(run=run, is_replay=True, fingerprint_matched=fingerprint_matched)


def is_valid_idempotency_key(key: str) -> bool:
    return bool(VISIBLE_ASCII_KEY.fullmatch(key))


def submit_request_fingerprint(raw_sql: str) -> str:
    return sha256(raw_sql.encode("utf-8")).hexdigest()


def finished_fields(run: QueryRun) -> dict[str, datetime | int]:
    finished_at = datetime.now(timezone.utc)
    duration_ms = max(0, int((finished_at - run.created_at).total_seconds() * 1_000))
    return {"finished_at": finished_at, "duration_ms": duration_ms}
