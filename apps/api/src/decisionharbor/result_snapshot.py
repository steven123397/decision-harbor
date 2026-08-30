import json
from collections.abc import Iterable
from dataclasses import dataclass

from decisionharbor.domain import JsonCell, QueryColumn, QueryResult


RESULT_MAX_ROWS = 500
RESULT_MAX_BYTES = 1_048_576
RESULT_TOO_LARGE_MESSAGE = "The query result is too large to store."


class ResultSnapshotTooLarge(ValueError):
    code = "result_too_large"


@dataclass(frozen=True)
class EncodedResultSnapshot:
    columns_json: str
    rows_json: str


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _encode_columns(columns: tuple[QueryColumn, ...]) -> str:
    return _compact_json([{"name": column.name, "type": column.type} for column in columns])


def _encode_row(row: tuple[JsonCell, ...]) -> str:
    return _compact_json(row)


def encode_result_snapshot(result: QueryResult) -> EncodedResultSnapshot:
    return EncodedResultSnapshot(
        columns_json=_encode_columns(result.columns),
        rows_json=_compact_json(result.rows),
    )


def build_result_snapshot(
    columns: tuple[QueryColumn, ...],
    rows: Iterable[tuple[JsonCell, ...]],
    max_rows: int = RESULT_MAX_ROWS,
) -> QueryResult:
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    row_limit = min(max_rows, RESULT_MAX_ROWS)
    columns_json = _encode_columns(columns)
    snapshot_size = len(b'{"columns":') + len(columns_json.encode("utf-8")) + len(b',"rows":[]}')
    if snapshot_size > RESULT_MAX_BYTES:
        raise ResultSnapshotTooLarge

    retained_rows: list[tuple[JsonCell, ...]] = []
    for row in rows:
        row_size = len(_encode_row(row).encode("utf-8"))
        if row_size > RESULT_MAX_BYTES:
            raise ResultSnapshotTooLarge
        if len(retained_rows) == row_limit:
            return QueryResult(columns=columns, rows=tuple(retained_rows), truncated=True)
        candidate_size = snapshot_size + row_size + (1 if retained_rows else 0)
        if candidate_size > RESULT_MAX_BYTES:
            if not retained_rows:
                raise ResultSnapshotTooLarge
            return QueryResult(columns=columns, rows=tuple(retained_rows), truncated=True)
        retained_rows.append(row)
        snapshot_size = candidate_size
    return QueryResult(columns=columns, rows=tuple(retained_rows), truncated=False)
