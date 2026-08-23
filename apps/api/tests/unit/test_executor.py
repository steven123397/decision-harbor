from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import NamedTuple

import pytest
import psycopg

from decisionharbor.executor import (
    ExecutionFailure,
    extract_snapshot,
    map_database_error,
    serialize_cell,
)
from decisionharbor.snapshots import SNAPSHOT_MAX_BYTES, SNAPSHOT_MAX_ROWS, SnapshotTooLarge


class ColumnDescription(NamedTuple):
    name: str
    type_code: int


TEXT_COLUMN = (ColumnDescription(name="value", type_code=25),)


def fetch_serving(
    rows: list[tuple[object, ...]],
) -> tuple[Callable[[], Sequence[object] | None], dict[str, int]]:
    """返回一个 fetch 桩，并记录实际提供给提取器的行数。"""
    state = {"next": 0, "served": 0}

    def fetch() -> Sequence[object] | None:
        if state["next"] >= len(rows):
            return None
        row = rows[state["next"]]
        state["next"] += 1
        state["served"] += 1
        return row

    return fetch, state


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


def test_maps_unrecognized_server_errors_to_stable_internal_error() -> None:
    error = psycopg.Error("unexpected server condition detail")
    error.sqlstate = "XX001"

    mapped = map_database_error(error)

    assert mapped.code == "internal_error"
    assert "unexpected server condition" not in mapped.message


def test_extraction_stops_reading_once_the_byte_budget_is_exhausted() -> None:
    wide = "a" * 600_000
    rows = [(wide,) for _ in range(600)]
    fetch, state = fetch_serving(rows)

    built = extract_snapshot(fetch, TEXT_COLUMN, SNAPSHOT_MAX_ROWS)

    # 第二行触发预算耗尽后立即停止读取，剩余 598 行不从游标进入内存。
    assert state["served"] == 2
    assert built.row_count == 1
    assert built.truncated is True


def test_extraction_stops_reading_after_the_row_cap() -> None:
    rows = [(f"row-{index}",) for index in range(SNAPSHOT_MAX_ROWS + 5)]
    fetch, state = fetch_serving(rows)

    built = extract_snapshot(fetch, TEXT_COLUMN, SNAPSHOT_MAX_ROWS)

    assert state["served"] == SNAPSHOT_MAX_ROWS + 1
    assert built.row_count == SNAPSHOT_MAX_ROWS
    assert built.truncated is True


def test_extraction_respects_a_run_level_row_limit_below_the_cap() -> None:
    rows = [(f"row-{index}",) for index in range(50)]
    fetch, state = fetch_serving(rows)

    built = extract_snapshot(fetch, TEXT_COLUMN, 3)

    assert state["served"] == 4
    assert built.row_count == 3
    assert built.truncated is True


def test_extraction_of_an_empty_result_is_not_truncated() -> None:
    fetch, state = fetch_serving([])

    built = extract_snapshot(fetch, TEXT_COLUMN, SNAPSHOT_MAX_ROWS)

    assert state["served"] == 0
    assert built.row_count == 0
    assert built.truncated is False
    assert json.loads(built.payload)["rows"] == []


def test_extraction_reports_structurally_oversized_rows() -> None:
    rows = [("small",), ("y" * (SNAPSHOT_MAX_BYTES + 1),), ("small",)]
    fetch, state = fetch_serving(rows)

    with pytest.raises(SnapshotTooLarge):
        extract_snapshot(fetch, TEXT_COLUMN, SNAPSHOT_MAX_ROWS)

    assert state["served"] == 2


def test_extraction_maps_unsupported_column_types_even_without_rows() -> None:
    jsonb_column = (ColumnDescription(name="payload", type_code=3802),)
    fetch, _ = fetch_serving([])

    with pytest.raises(ExecutionFailure) as caught:
        extract_snapshot(fetch, jsonb_column, SNAPSHOT_MAX_ROWS)

    assert caught.value.code == "unsupported_result_type"


def test_extraction_maps_unsupported_cell_types_to_a_failure() -> None:
    jsonb_column = (ColumnDescription(name="payload", type_code=3802),)
    fetch, _ = fetch_serving((('{"kind":"x"}',),))

    with pytest.raises(ExecutionFailure) as caught:
        extract_snapshot(fetch, jsonb_column, SNAPSHOT_MAX_ROWS)

    assert caught.value.code == "unsupported_result_type"


def test_extraction_reports_oversized_column_definitions() -> None:
    # 真实 PostgreSQL 的标识符与列数上限使该形态不可达；接缝级证据补齐该分支。
    wide_columns = tuple(
        ColumnDescription(name="column_" + "x" * 200, type_code=25)
        for _ in range(SNAPSHOT_MAX_BYTES // 100)
    )
    fetch, state = fetch_serving([("small",)])

    with pytest.raises(SnapshotTooLarge):
        extract_snapshot(fetch, wide_columns, SNAPSHOT_MAX_ROWS)

    assert state["served"] == 0
