from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import TypeAlias
from uuid import uuid4


JsonCell: TypeAlias = None | bool | int | float | str

# A result snapshot is always a bounded, immutable prefix: no more than this
# many rows and no more than this many bytes of the compact UTF-8 JSON described
# by the external contract.
RESULT_MAX_ROWS = 500
RESULT_MAX_BYTES = 1_048_576

# A stored snapshot stays readable for this long after the run finished. The
# window is measured from the `finished_at` the database recorded, so neither
# the moment a client asks nor the moment the worker published it decides
# whether a result is still being retained.
RESULT_RETENTION = timedelta(hours=24)

TERMINAL_STATUSES = frozenset({"rejected", "succeeded", "failed", "cancelled"})

# The three ways a result read can fail. Each one tells the client something
# different: keep polling, give up on this run, or accept that a result which
# existed is no longer stored.
RESULT_NOT_READY = "result_not_ready"
RESULT_UNAVAILABLE = "result_unavailable"
RESULT_EXPIRED = "result_expired"

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


class ResultTooLarge(Exception):
    """A result that cannot be stored as a bounded snapshot prefix."""


def encode_json(value: object) -> bytes:
    """Encode a value as the compact UTF-8 JSON the snapshot contract fixes.

    Non-ASCII text stays UTF-8 encoded rather than escaped, so the measured
    bytes are the bytes a client receives.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_snapshot_columns(columns: tuple[QueryColumn, ...]) -> bytes:
    """The column definitions of a snapshot, as the bytes the budget counts."""
    return encode_json([{"name": column.name, "type": column.type} for column in columns])


class ResultSnapshotBuilder:
    """Accumulates the longest result prefix that fits both snapshot bounds.

    Rows are taken in database order until the next one would break either
    bound. A result whose column definitions, first row, or any single row
    cannot fit the byte budget is rejected outright: the query run fails instead
    of persisting partial rows or partial cells.

    The column definitions count together with the structure every snapshot
    carries, because a snapshot with no rows is still stored, and a stored
    snapshot must never measure more than the budget.
    """

    def __init__(self, columns: tuple[QueryColumn, ...], *, max_rows: int = RESULT_MAX_ROWS) -> None:
        self._columns = columns
        self._max_rows = max_rows
        self._rows: list[tuple[JsonCell, ...]] = []
        self._truncated = False
        # Every accepted row adds its own bytes plus the comma that separates it
        # from the previous one, so the empty snapshot is the starting size.
        self._size = (
            len(b'{"columns":') + len(encode_snapshot_columns(columns)) + len(b',"rows":[]}')
        )
        if self._size > RESULT_MAX_BYTES:
            raise ResultTooLarge

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def truncated(self) -> bool:
        """Whether a row was left out because it broke the row or byte bound."""
        return self._truncated

    def add(self, row: tuple[JsonCell, ...]) -> bool:
        """Take the next row, reporting whether it still fits the snapshot."""
        # The row bound is checked first: a row beyond it is only read to prove
        # there is more to read, so its own size cannot fail the run.
        if self.row_count >= self._max_rows:
            self._truncated = True
            return False
        row_bytes = encode_json(list(row))
        if len(row_bytes) > RESULT_MAX_BYTES:
            raise ResultTooLarge
        size = self._size + len(row_bytes) + (1 if self._rows else 0)
        if size > RESULT_MAX_BYTES:
            if not self._rows:
                raise ResultTooLarge
            self._truncated = True
            return False
        self._rows.append(row)
        self._size = size
        return True

    def build(self) -> QueryResult:
        return QueryResult(columns=self._columns, rows=tuple(self._rows), truncated=self._truncated)


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
class StoredResult:
    """A query run together with the result snapshot stored for it, if any.

    `snapshot` is None both for a run that never produced a result and for one
    whose result the retention cleaner already removed; the run's own facts say
    which of the two it is. `result_expired` is what the database says about the
    retention window at the moment of this read.
    """

    run: QueryRun
    snapshot: QueryResult | None
    result_expired: bool


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


def result_read_failure(run: QueryRun, *, has_snapshot: bool, expired: bool) -> str | None:
    """Why a query run's result cannot be read, or None when it can.

    A run that has not reached a terminal state may still produce a result, so
    its failure is not ready rather than unavailable. Only a succeeded run ever
    carried a result, so only its result can age out of the retention window:
    every other terminal run reports a result that never existed, however long
    ago it finished.

    `expired` is decided where the retention window is measured, in the database
    that also stores `finished_at`, so a read and the cleanup that follows it
    cannot disagree about whether a result is still being retained.
    """
    if run.status not in TERMINAL_STATUSES:
        return RESULT_NOT_READY
    if run.status == "succeeded" and expired:
        return RESULT_EXPIRED
    if not has_snapshot:
        return RESULT_UNAVAILABLE
    return None


def finished_fields(run: QueryRun) -> dict[str, datetime | int]:
    finished_at = datetime.now(timezone.utc)
    duration_ms = max(0, int((finished_at - run.created_at).total_seconds() * 1_000))
    return {"finished_at": finished_at, "duration_ms": duration_ms}
