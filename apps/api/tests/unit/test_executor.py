from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import psycopg

from decisionharbor.executor import ExecutionFailure, map_database_error, serialize_cell


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
