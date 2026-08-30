import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import psycopg

from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.executor import (
    ExecutionFailure,
    map_database_error,
    serialize_cell,
)
from decisionharbor.result_snapshot import (
    RESULT_MAX_BYTES,
    ResultSnapshotTooLarge,
    build_result_snapshot,
    encode_result_snapshot,
)


def compact_snapshot_bytes(result: QueryResult) -> bytes:
    return json.dumps(
        {
            "columns": [{"name": column.name, "type": column.type} for column in result.columns],
            "rows": result.rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def single_text_value_for_snapshot_size(size: int, fill: str = "x") -> str:
    empty = QueryResult(
        columns=(QueryColumn(name="value", type="text"),),
        rows=(("",),),
        truncated=False,
    )
    available_bytes = size - len(compact_snapshot_bytes(empty))
    encoded_fill_size = len(fill.encode("utf-8"))
    return fill * (available_bytes // encoded_fill_size) + "x" * (available_bytes % encoded_fill_size)


@pytest.mark.parametrize(
    ("value", "type_oid", "expected"),
    [
        (None, 25, None),
        (True, 16, True),
        (42, 23, 42),
        (42, 20, "42"),
        (Decimal("19.90"), 1700, "19.90"),
        (date(2026, 7, 20), 1082, "2026-07-20"),
        (datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc), 1184, "2026-07-20T08:00:00+00:00"),
        ("Central", 25, "Central"),
    ],
)
def test_serializes_supported_cells_without_losing_numeric_precision(
    value: object,
    type_oid: int,
    expected: object,
) -> None:
    assert serialize_cell(value, type_oid) == expected


def test_rejects_unsupported_result_type_instead_of_stringifying() -> None:
    with pytest.raises(ExecutionFailure) as caught:
        serialize_cell({"opaque": True}, 3802)

    assert caught.value.code == "unsupported_result_type"
    assert "opaque" not in caught.value.message


def test_maps_connection_failure_without_leaking_driver_detail() -> None:
    mapped = map_database_error(psycopg.OperationalError("password and host detail"))

    assert mapped.code == "analytics_unavailable"
    assert "password" not in mapped.message


def test_result_snapshot_keeps_the_first_500_rows_in_order() -> None:
    columns = (QueryColumn(name="position", type="integer"),)

    result = build_result_snapshot(columns, ((position,) for position in range(501)))

    assert result == QueryResult(
        columns=columns,
        rows=tuple((position,) for position in range(500)),
        truncated=True,
    )


def test_result_snapshot_uses_one_canonical_compact_json_encoding() -> None:
    result = QueryResult(
        columns=(QueryColumn(name="列", type="text"),),
        rows=(("值",),),
        truncated=False,
    )

    encoded = encode_result_snapshot(result)

    assert encoded.columns_json == '[{"name":"列","type":"text"}]'
    assert encoded.rows_json == '[["值"]]'


def test_result_snapshot_accepts_exactly_500_rows_without_truncation() -> None:
    columns = (QueryColumn(name="position", type="integer"),)

    result = build_result_snapshot(columns, ((position,) for position in range(500)))

    assert len(result.rows) == 500
    assert result.rows[-1] == (499,)
    assert result.truncated is False


def test_result_snapshot_accepts_exactly_one_mibibyte_with_multibyte_utf8() -> None:
    columns = (QueryColumn(name="value", type="text"),)
    value = single_text_value_for_snapshot_size(RESULT_MAX_BYTES, fill="数")

    result = build_result_snapshot(columns, ((value,),))

    assert result.rows == ((value,),)
    assert result.truncated is False
    assert len(compact_snapshot_bytes(result)) == RESULT_MAX_BYTES


def test_result_snapshot_rejects_a_first_row_one_byte_over_the_limit() -> None:
    columns = (QueryColumn(name="value", type="text"),)
    value = single_text_value_for_snapshot_size(RESULT_MAX_BYTES + 1)

    with pytest.raises(ResultSnapshotTooLarge) as caught:
        build_result_snapshot(columns, ((value,),))

    assert caught.value.code == "result_too_large"


def test_result_snapshot_keeps_the_longest_prefix_on_cumulative_byte_overflow() -> None:
    columns = (QueryColumn(name="value", type="text"),)
    row = ("x" * 600_000,)

    result = build_result_snapshot(columns, (row, row))

    assert result == QueryResult(columns=columns, rows=(row,), truncated=True)


def test_result_snapshot_rejects_oversized_columns_without_rows() -> None:
    columns = (QueryColumn(name="x" * RESULT_MAX_BYTES, type="text"),)

    with pytest.raises(ResultSnapshotTooLarge) as caught:
        build_result_snapshot(columns, ())

    assert caught.value.code == "result_too_large"


def test_result_snapshot_rejects_an_oversized_later_row_instead_of_publishing_a_prefix() -> None:
    columns = (QueryColumn(name="value", type="text"),)

    with pytest.raises(ResultSnapshotTooLarge) as caught:
        build_result_snapshot(columns, (("kept",), ("x" * (RESULT_MAX_BYTES + 1),)))

    assert caught.value.code == "result_too_large"
