import json

import pytest

from decisionharbor.domain import (
    RESULT_MAX_BYTES,
    RESULT_MAX_ROWS,
    QueryColumn,
    ResultSnapshotBuilder,
    ResultTooLarge,
    encode_json,
)


COLUMNS = (QueryColumn(name="value", type="text"),)


def test_the_snapshot_bounds_are_the_ones_the_contract_fixes() -> None:
    assert (RESULT_MAX_ROWS, RESULT_MAX_BYTES) == (500, 1_048_576)


def snapshot_bytes(columns: tuple[QueryColumn, ...], rows: list[list[object]]) -> bytes:
    """The snapshot as the external contract describes it: compact UTF-8 JSON."""
    return json.dumps(
        {
            "columns": [{"name": column.name, "type": column.type} for column in columns],
            "rows": rows,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def columns_filling_budget(over_by: int = 0) -> tuple[QueryColumn, ...]:
    """Column definitions whose snapshot with no rows measures the budget plus `over_by`."""
    single = (QueryColumn(name="c", type="text"),)
    padding = RESULT_MAX_BYTES - len(snapshot_bytes(single, [])) + over_by
    return (QueryColumn(name="c" + "x" * padding, type="text"),)


def filling_rows(row_count: int, over_by: int = 0) -> tuple[tuple[str, ...], ...]:
    """One-cell rows whose snapshot measures exactly the byte budget, plus `over_by`."""
    empty = len(snapshot_bytes(COLUMNS, [[""]] * row_count))
    padding = RESULT_MAX_BYTES - empty + over_by
    lengths = [padding // row_count] * row_count
    lengths[0] += padding % row_count
    return tuple((("x" * length),) for length in lengths)


def row_of_bytes(size: int) -> tuple[str, ...]:
    """A one-cell row whose JSON array measures exactly `size` bytes."""
    return (("x" * (size - len(encode_json([""]))),))


def build(rows: tuple[tuple[str, ...], ...], *, max_rows: int = RESULT_MAX_ROWS) -> ResultSnapshotBuilder:
    builder = ResultSnapshotBuilder(COLUMNS, max_rows=max_rows)
    for row in rows:
        if not builder.add(row):
            break
    return builder


def test_keeps_exactly_the_row_limit_without_marking_truncation() -> None:
    builder = build((("x",),) * RESULT_MAX_ROWS)

    assert builder.row_count == RESULT_MAX_ROWS
    assert builder.truncated is False
    assert builder.build().truncated is False


def test_marks_truncation_when_a_row_exists_beyond_the_row_limit() -> None:
    builder = build((("x",),) * (RESULT_MAX_ROWS + 1))

    assert builder.row_count == RESULT_MAX_ROWS
    assert builder.truncated is True


def test_keeps_a_snapshot_that_exactly_fills_the_byte_budget() -> None:
    rows = filling_rows(1)

    builder = build(rows)

    assert builder.truncated is False
    assert len(snapshot_bytes(COLUMNS, [list(row) for row in builder.build().rows])) == RESULT_MAX_BYTES


def test_truncates_the_longest_prefix_when_the_next_row_exceeds_the_budget_by_one_byte() -> None:
    rows = filling_rows(2, over_by=1)

    builder = build(rows)

    assert builder.row_count == 1
    assert builder.truncated is True
    assert builder.build().rows == rows[:1]


def test_keeps_column_definitions_that_exactly_fill_the_budget() -> None:
    columns = columns_filling_budget()

    result = ResultSnapshotBuilder(columns).build()

    assert result.rows == ()
    assert result.truncated is False
    assert len(snapshot_bytes(columns, [])) == RESULT_MAX_BYTES


def test_rejects_column_definitions_one_byte_beyond_the_budget() -> None:
    with pytest.raises(ResultTooLarge):
        ResultSnapshotBuilder(columns_filling_budget(over_by=1))


def test_rejects_column_definitions_that_alone_exceed_the_budget() -> None:
    wide_columns = tuple(
        QueryColumn(name="c" * 400, type="timestamp without time zone")
        for _ in range(RESULT_MAX_BYTES // 400)
    )

    with pytest.raises(ResultTooLarge):
        ResultSnapshotBuilder(wide_columns)


def test_rejects_a_first_row_that_pushes_the_snapshot_over_the_budget() -> None:
    with pytest.raises(ResultTooLarge):
        ResultSnapshotBuilder(COLUMNS).add(filling_rows(1, over_by=1)[0])


def test_rejects_a_single_row_that_alone_exceeds_the_budget() -> None:
    with pytest.raises(ResultTooLarge):
        ResultSnapshotBuilder(COLUMNS).add(row_of_bytes(RESULT_MAX_BYTES + 1))


def test_rejects_a_later_row_that_alone_exceeds_the_budget_instead_of_truncating() -> None:
    builder = ResultSnapshotBuilder(COLUMNS)
    builder.add(("small",))

    with pytest.raises(ResultTooLarge):
        builder.add(row_of_bytes(RESULT_MAX_BYTES + 1))


def test_a_later_row_of_exactly_the_budget_truncates_instead_of_failing() -> None:
    builder = ResultSnapshotBuilder(COLUMNS)
    builder.add(("small",))

    assert builder.add(row_of_bytes(RESULT_MAX_BYTES)) is False
    assert builder.truncated is True
    assert builder.row_count == 1


def test_a_row_beyond_the_row_bound_only_marks_truncation() -> None:
    builder = build((("x",),) * RESULT_MAX_ROWS)

    assert builder.add(row_of_bytes(RESULT_MAX_BYTES + 1)) is False
    assert builder.row_count == RESULT_MAX_ROWS
    assert builder.truncated is True


def test_counts_multibyte_text_as_utf8_bytes_rather_than_characters() -> None:
    # 100_000 characters of "数" are 300_000 UTF-8 bytes, so only three rows fit
    # in a 1 MiB snapshot even though ten rows would fit by character count.
    rows = tuple((("数" * 100_000),) for _ in range(4))

    builder = build(rows)

    assert builder.row_count == 3
    assert builder.truncated is True


def test_encodes_json_compactly_and_without_escaping_non_ascii() -> None:
    assert encode_json({"columns": [{"name": "区域", "type": "text"}], "rows": [["华东"], [None, True, 1]]}) == (
        '{"columns":[{"name":"区域","type":"text"}],"rows":[["华东"],[null,true,1]]}'.encode("utf-8")
    )
