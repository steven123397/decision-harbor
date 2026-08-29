import pytest

from decisionharbor.domain import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    is_valid_idempotency_key,
    submit_request_fingerprint,
)


@pytest.mark.parametrize(
    "key",
    [
        "a",
        "submit-1",
        "aA0-_.~!*'()",
        "k" * IDEMPOTENCY_KEY_MAX_LENGTH,
    ],
)
def test_visible_ascii_keys_within_the_length_bound_are_accepted(key: str) -> None:
    assert is_valid_idempotency_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "k" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1),
        " leading-space",
        "trailing-space ",
        "key with spaces",
        "key\nvalue",
        "key\tvalue",
        "key\x00value",
        "key\x7fvalue",
        "ключ",
    ],
)
def test_keys_outside_visible_ascii_or_the_length_bound_are_rejected(key: str) -> None:
    assert not is_valid_idempotency_key(key)


def test_fingerprint_is_stable_for_the_same_sql_and_distinct_for_any_change() -> None:
    sql = "SELECT count(*) AS customer_count FROM customers"

    assert submit_request_fingerprint(sql) == submit_request_fingerprint(sql)
    assert submit_request_fingerprint(sql) != submit_request_fingerprint(f"{sql} ")
    assert submit_request_fingerprint("SELECT 1") != submit_request_fingerprint("SELECT 2")


def test_fingerprint_fits_the_persisted_column() -> None:
    assert len(submit_request_fingerprint("SELECT 1")) == 64
