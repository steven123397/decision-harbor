import json

import pytest

from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.snapshots import (
    SNAPSHOT_MAX_BYTES,
    SNAPSHOT_MAX_ROWS,
    SnapshotTooLarge,
    build_snapshot,
)


def result_of(columns: tuple[QueryColumn, ...], rows: tuple[tuple[object, ...], ...], truncated: bool = False) -> QueryResult:
    return QueryResult(columns=columns, rows=rows, truncated=truncated)  # type: ignore[arg-type]


SINGLE_TEXT_COLUMN = (QueryColumn(name="value", type="text"),)
# '{"columns":[{"name":"value","type":"text"}],"rows":[]}' 的固定前缀开销
BASE_BYTES = (
    len('{"columns":'.encode())
    + len('[{"name":"value","type":"text"}]'.encode())
    + len(',"rows":['.encode())
    + len("]}".encode())
)


def test_snapshot_uses_the_spec_compact_json_shape() -> None:
    built = build_snapshot(
        result_of(SINGLE_TEXT_COLUMN, (("é", 1, None),)),
    )

    assert built.payload == '{"columns":[{"name":"value","type":"text"}],"rows":[["é",1,null]]}'
    assert built.byte_size == len(built.payload.encode("utf-8"))
    assert built.row_count == 1
    assert built.truncated is False
    assert json.loads(built.payload) == {"columns": [{"name": "value", "type": "text"}], "rows": [["é", 1, None]]}


def test_snapshot_exactly_at_the_byte_budget_succeeds() -> None:
    budget = BASE_BYTES
    first = "a" * 100
    first_row_bytes = len(('["' + first + '"]').encode())
    second_length = SNAPSHOT_MAX_BYTES - budget - first_row_bytes - 1 - 4
    second = "b" * second_length
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, ((first,), (second,))))

    assert built.byte_size == SNAPSHOT_MAX_BYTES
    assert built.row_count == 2
    assert built.truncated is False


def test_snapshot_one_byte_over_budget_keeps_the_longest_prefix() -> None:
    budget = BASE_BYTES
    first = "a" * 100
    first_row_bytes = len(('["' + first + '"]').encode())
    second_length = SNAPSHOT_MAX_BYTES - budget - first_row_bytes - 1 - 4 + 1
    second = "b" * second_length
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, ((first,), (second,))))

    assert built.row_count == 1
    assert built.truncated is True
    assert built.byte_size == budget + first_row_bytes
    assert json.loads(built.payload)["rows"] == [[first]]


def test_oversized_column_definition_fails_without_partial_rows() -> None:
    wide_columns = tuple(
        QueryColumn(name=f"column_{index}_" + "x" * 200, type="text")
        for index in range(SNAPSHOT_MAX_BYTES // 100)
    )

    with pytest.raises(SnapshotTooLarge):
        build_snapshot(result_of(wide_columns, ()))


def test_oversized_first_row_fails_without_partial_cells() -> None:
    with pytest.raises(SnapshotTooLarge):
        build_snapshot(result_of(SINGLE_TEXT_COLUMN, (("x" * SNAPSHOT_MAX_BYTES,),)))


def test_oversized_single_row_among_rows_fails_the_whole_snapshot() -> None:
    with pytest.raises(SnapshotTooLarge):
        build_snapshot(result_of(SINGLE_TEXT_COLUMN, (("small",), ("y" * SNAPSHOT_MAX_BYTES,), ("small",))))


def test_multibyte_content_counts_bytes_not_characters() -> None:
    row_bytes = len(('["' + "é" * 300_000 + '"]').encode("utf-8"))
    assert row_bytes < SNAPSHOT_MAX_BYTES
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, (("é" * 300_000,), ("é" * 300_000,))))

    assert built.row_count == 1
    assert built.truncated is True
    assert built.byte_size == BASE_BYTES + row_bytes


def test_row_cap_keeps_500_rows_and_marks_truncated() -> None:
    rows = tuple((f"row-{index}",) for index in range(SNAPSHOT_MAX_ROWS + 100))
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, rows), max_rows=5_000)

    assert built.row_count == SNAPSHOT_MAX_ROWS
    assert built.truncated is True


def test_executor_truncation_flag_is_preserved() -> None:
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, (("only",),), truncated=True))

    assert built.truncated is True


def test_empty_rows_snapshot_is_valid() -> None:
    built = build_snapshot(result_of(SINGLE_TEXT_COLUMN, ()))

    assert built.row_count == 0
    assert built.truncated is False
    assert json.loads(built.payload)["rows"] == []
