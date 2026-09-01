"""历史分页合同：不透明游标编解码与 limit 边界。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import base64
import json

from decisionharbor.domain import HistoryCursor
from decisionharbor.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    decode_cursor,
    encode_cursor,
    parse_limit,
)


def make_cursor() -> HistoryCursor:
    return HistoryCursor(
        created_at=datetime(2026, 9, 1, 12, 30, 45, 123456, tzinfo=timezone.utc),
        id=str(uuid4()),
    )


def test_cursor_round_trip_preserves_the_keyset_position() -> None:
    cursor = make_cursor()

    decoded = decode_cursor(encode_cursor(cursor))

    assert decoded == cursor


def test_cursor_is_url_safe_and_carries_no_padding() -> None:
    encoded = encode_cursor(make_cursor())

    assert "=" not in encoded
    assert all(character.isalnum() or character in "-_" for character in encoded)


def test_decode_rejects_inputs_the_server_never_generated() -> None:
    def b64(payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    naive = datetime(2026, 9, 1, 12, 30, 45).isoformat()
    wrong_version = json.dumps({"v": 2, "created_at": naive, "id": str(uuid4())})
    missing_keys = json.dumps({"v": 1})
    bad_uuid = json.dumps({"v": 1, "created_at": naive, "id": "not-a-uuid"})
    naive_datetime = json.dumps({"v": 1, "created_at": naive, "id": str(uuid4())})

    for raw in (
        "",
        "not-a-cursor",
        "!!!!!",
        b64(b"plain text"),
        b64(b"{broken json"),
        b64(json.dumps(["a", "list"]).encode()),
        b64(wrong_version.encode()),
        b64(missing_keys.encode()),
        b64(bad_uuid.encode()),
        b64(naive_datetime.encode()),
    ):
        assert decode_cursor(raw) is None, raw


def test_decode_accepts_the_boundary_formats_the_server_produces() -> None:
    # 无微秒与正偏移时区都是合法 created_at 形态，必须能无损往返。
    for created_at in (
        datetime(2026, 9, 1, 12, 30, 45, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 12, 30, 45, 987654, tzinfo=timezone(timedelta(hours=8))),
    ):
        cursor = HistoryCursor(created_at=created_at, id=str(uuid4()))

        assert decode_cursor(encode_cursor(cursor)) == cursor


def test_parse_limit_defaults_and_bounds() -> None:
    assert parse_limit(None) == DEFAULT_PAGE_LIMIT
    assert DEFAULT_PAGE_LIMIT == 20
    assert MAX_PAGE_LIMIT == 100
    assert parse_limit("1") == 1
    assert parse_limit("20") == 20
    assert parse_limit("100") == 100


def test_parse_limit_rejects_out_of_range_and_non_integer_values() -> None:
    for raw in ("0", "-1", "101", "", "abc", "1.5", " 5", "1e2", "+5", "١٢٣", "007x"):
        assert parse_limit(raw) is None, raw
