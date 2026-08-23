import json

import pytest

from decisionharbor.domain import QueryColumn
from decisionharbor.snapshots import (
    SNAPSHOT_MAX_BYTES,
    SNAPSHOT_MAX_ROWS,
    SnapshotBuilder,
    SnapshotTooLarge,
    build_snapshot,
)


SINGLE_TEXT_COLUMN = (QueryColumn(name="value", type="text"),)
# '{"columns":[{"name":"value","type":"text"}],"rows":[]}' 的固定前缀开销
BASE_BYTES = (
    len('{"columns":'.encode())
    + len('[{"name":"value","type":"text"}]'.encode())
    + len(',"rows":['.encode())
    + len("]}".encode())
)


def test_snapshot_uses_the_spec_compact_json_shape() -> None:
    built = build_snapshot(SINGLE_TEXT_COLUMN, (("é", 1, None),))

    assert built.payload == '{"columns":[{"name":"value","type":"text"}],"rows":[["é",1,null]]}'
    assert built.byte_size == len(built.payload.encode("utf-8"))
    assert built.row_count == 1
    assert built.truncated is False
    assert json.loads(built.payload) == {
        "columns": [{"name": "value", "type": "text"}],
        "rows": [["é", 1, None]],
    }


def test_snapshot_preserves_adr_0007_value_types() -> None:
    columns = (
        QueryColumn(name="flag", type="boolean"),
        QueryColumn(name="amount", type="integer"),
        QueryColumn(name="total", type="bigint"),
        QueryColumn(name="price", type="numeric"),
        QueryColumn(name="label", type="text"),
        QueryColumn(name="day", type="date"),
        QueryColumn(name="moment", type="timestamp with time zone"),
    )
    row = (True, 7, "9007199254740993", "19.90", "Central", "2026-07-20", "2026-07-20T08:00:00+00:00")

    built = build_snapshot(columns, (row,))

    assert json.loads(built.payload)["rows"] == [[True, 7, "9007199254740993", "19.90", "Central", "2026-07-20", "2026-07-20T08:00:00+00:00"]]


def test_snapshot_exactly_at_the_byte_budget_succeeds() -> None:
    first = "a" * 100
    first_row_bytes = len(('["' + first + '"]').encode())
    second_length = SNAPSHOT_MAX_BYTES - BASE_BYTES - first_row_bytes - 1 - 4
    second = "b" * second_length
    built = build_snapshot(SINGLE_TEXT_COLUMN, ((first,), (second,)))

    assert built.byte_size == SNAPSHOT_MAX_BYTES
    assert built.row_count == 2
    assert built.truncated is False


def test_snapshot_one_byte_over_budget_keeps_the_longest_prefix() -> None:
    first = "a" * 100
    first_row_bytes = len(('["' + first + '"]').encode())
    second_length = SNAPSHOT_MAX_BYTES - BASE_BYTES - first_row_bytes - 1 - 4 + 1
    second = "b" * second_length
    built = build_snapshot(SINGLE_TEXT_COLUMN, ((first,), (second,)))

    assert built.row_count == 1
    assert built.truncated is True
    assert built.byte_size == BASE_BYTES + first_row_bytes
    assert json.loads(built.payload)["rows"] == [[first]]


def test_snapshot_exactly_at_the_row_cap_succeeds() -> None:
    rows = tuple((f"row-{index}",) for index in range(SNAPSHOT_MAX_ROWS))

    built = build_snapshot(SINGLE_TEXT_COLUMN, rows)

    assert built.row_count == SNAPSHOT_MAX_ROWS
    assert built.truncated is False


def test_snapshot_one_row_over_cap_keeps_the_stable_prefix() -> None:
    rows = tuple((f"row-{index}",) for index in range(SNAPSHOT_MAX_ROWS + 1))

    built = build_snapshot(SINGLE_TEXT_COLUMN, rows)

    assert built.row_count == SNAPSHOT_MAX_ROWS
    assert built.truncated is True
    payload_rows = json.loads(built.payload)["rows"]
    assert payload_rows[0] == ["row-0"]
    assert payload_rows[-1] == [f"row-{SNAPSHOT_MAX_ROWS - 1}"]


def test_run_level_row_cap_below_the_snapshot_cap_limits_the_prefix() -> None:
    rows = tuple((f"row-{index}",) for index in range(10))

    built = build_snapshot(SINGLE_TEXT_COLUMN, rows, max_rows=3)

    assert built.row_count == 3
    assert built.truncated is True


def test_oversized_column_definition_fails_without_partial_rows() -> None:
    wide_columns = tuple(
        QueryColumn(name=f"column_{index}_" + "x" * 200, type="text")
        for index in range(SNAPSHOT_MAX_BYTES // 100)
    )

    with pytest.raises(SnapshotTooLarge):
        build_snapshot(wide_columns, ())


def test_first_row_that_breaks_the_budget_fails_without_partial_rows() -> None:
    # 行本身未超过 1 MiB，但加入首行后快照整体超过 1 MiB。
    first = "a" * (SNAPSHOT_MAX_BYTES - BASE_BYTES - 4 + 2)

    with pytest.raises(SnapshotTooLarge):
        build_snapshot(SINGLE_TEXT_COLUMN, ((first,),))


def test_single_row_alone_over_the_budget_fails() -> None:
    with pytest.raises(SnapshotTooLarge):
        build_snapshot(SINGLE_TEXT_COLUMN, (("x" * (SNAPSHOT_MAX_BYTES + 1),),))


def test_oversized_single_row_among_rows_fails_the_whole_snapshot() -> None:
    with pytest.raises(SnapshotTooLarge):
        build_snapshot(SINGLE_TEXT_COLUMN, (("small",), ("y" * (SNAPSHOT_MAX_BYTES + 1),), ("small",)))


def test_multibyte_content_counts_bytes_not_characters() -> None:
    row_bytes = len(('["' + "é" * 300_000 + '"]').encode("utf-8"))
    assert row_bytes < SNAPSHOT_MAX_BYTES
    built = build_snapshot(SINGLE_TEXT_COLUMN, (("é" * 300_000,), ("é" * 300_000,)))

    assert built.row_count == 1
    assert built.truncated is True
    assert built.byte_size == BASE_BYTES + row_bytes


def test_empty_rows_snapshot_is_valid() -> None:
    built = build_snapshot(SINGLE_TEXT_COLUMN, ())

    assert built.row_count == 0
    assert built.truncated is False
    assert json.loads(built.payload)["rows"] == []


def test_builder_signals_the_caller_to_stop_reading_at_the_budget() -> None:
    builder = SnapshotBuilder(SINGLE_TEXT_COLUMN)
    wide = "a" * 600_000

    assert builder.add_row((wide,)) is True
    assert builder.add_row((wide,)) is False

    # 预算耗尽后前缀已固定：更小的后续行也不能混入，否则破坏稳定前缀语义。
    assert builder.add_row(("tiny",)) is False
    built = builder.build()
    assert built.row_count == 1
    assert built.truncated is True
    assert json.loads(built.payload)["rows"] == [[wide]]


def test_builder_rejects_non_positive_row_limits() -> None:
    with pytest.raises(ValueError):
        SnapshotBuilder(SINGLE_TEXT_COLUMN, max_rows=0)
