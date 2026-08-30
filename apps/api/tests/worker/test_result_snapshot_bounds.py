import json
import os
from time import monotonic, sleep
from uuid import uuid4

import psycopg
from psycopg import rows
import pytest

from decisionharbor.domain import RESULT_MAX_BYTES


pytestmark = pytest.mark.worker


TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
WAIT_SECONDS = 60
POLL_SECONDS = 0.1

# Every large-value query below returns one text column named `value`, so a
# snapshot measures as `{"columns":[{"name":"value","type":"text"}],"rows":[...]}`.
VALUE_COLUMNS = [{"name": "value", "type": "text"}]

# 100_000 characters of "数" are 300_000 UTF-8 bytes, so three rows fill a 1 MiB
# snapshot while ten would fit if characters were counted instead of bytes.
MULTIBYTE_CHARACTERS = 100_000

TYPES_SQL = (
    "SELECT o.id AS order_id, o.currency AS currency, o.ordered_at AS ordered_at, "
    "o.ordered_at::date AS ordered_on, oi.quantity AS quantity, oi.unit_price AS unit_price, "
    "p.active AS active, c.segment AS segment "
    "FROM orders o JOIN order_items oi ON oi.order_id = o.id "
    "JOIN products p ON p.id = oi.product_id JOIN customers c ON c.id = o.customer_id "
    "ORDER BY o.id, oi.id LIMIT 1"
)


def platform_url() -> str:
    return os.environ["PLATFORM_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def insert_queued_run(raw_sql: str, *, statement_timeout_ms: int = 5_000, max_rows: int = 500) -> str:
    run_id = str(uuid4())
    with psycopg.connect(platform_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at
            ) VALUES (%s, %s, 'queued', 'allowed', 'policy-v1', %s, %s, now())
            """,
            (run_id, raw_sql, statement_timeout_ms, max_rows),
        )
    return run_id


def read_run(run_id: str) -> dict[str, object]:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT status, error_code, returned_row_count, result_truncated
            FROM query_runs WHERE id = %s
            """,
            (run_id,),
        ).fetchone()


def read_snapshot(run_id: str) -> dict[str, object] | None:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT result_columns, result_rows, truncated
            FROM query_run_results WHERE query_run_id = %s
            """,
            (run_id,),
        ).fetchone()


def wait_for_terminal(run_id: str) -> dict[str, object]:
    deadline = monotonic() + WAIT_SECONDS
    while monotonic() < deadline:
        facts = read_run(run_id)
        if facts["status"] in TERMINAL_STATUSES:
            return facts
        sleep(POLL_SECONDS)
    raise AssertionError(f"query run {run_id} never reached a terminal state: {read_run(run_id)}")


def snapshot_bytes(*rows_: list[object]) -> bytes:
    """The snapshot as the external contract measures it: compact UTF-8 JSON."""
    return json.dumps(
        {"columns": VALUE_COLUMNS, "rows": list(rows_)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def repeat_length_filling_budget(row_count: int, over_by: int = 0) -> int:
    """A `repeat` length that makes `row_count` equal rows measure the budget plus `over_by`."""
    empty = len(snapshot_bytes(*([[""]] * row_count)))
    padding = RESULT_MAX_BYTES - empty + over_by
    assert padding % row_count == 0, "the budget cannot be split into equal rows"
    return padding // row_count


def test_five_hundred_rows_are_kept_without_marking_truncation() -> None:
    run_id = insert_queued_run("SELECT id FROM order_items ORDER BY id LIMIT 500")

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 500
    assert facts["result_truncated"] is False
    assert len(read_snapshot(run_id)["result_rows"]) == 500


def test_a_five_hundred_and_first_row_truncates_the_snapshot() -> None:
    run_id = insert_queued_run("SELECT id FROM order_items ORDER BY id LIMIT 501")

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["returned_row_count"] == 500
    assert facts["result_truncated"] is True
    assert snapshot["truncated"] is True
    assert [row[0] for row in snapshot["result_rows"]] == [str(number) for number in range(1, 501)]


def test_a_snapshot_that_exactly_fills_the_byte_budget_is_stored_whole() -> None:
    run_id = insert_queued_run(f"SELECT repeat('x', {repeat_length_filling_budget(1)}) AS value")

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert facts["result_truncated"] is False
    assert snapshot["result_columns"] == VALUE_COLUMNS
    assert len(snapshot_bytes(*snapshot["result_rows"])) == RESULT_MAX_BYTES


def test_one_byte_beyond_the_byte_budget_keeps_the_longest_prefix() -> None:
    length = repeat_length_filling_budget(2, over_by=1)
    run_id = insert_queued_run(
        f"SELECT repeat('x', {length}) AS value FROM orders o WHERE o.id IN (1, 2) ORDER BY o.id"
    )

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 1
    assert facts["result_truncated"] is True
    stored = snapshot["result_rows"]
    assert len(snapshot_bytes(*stored)) <= RESULT_MAX_BYTES
    assert len(snapshot_bytes(*stored, stored[0])) > RESULT_MAX_BYTES


def test_a_first_row_that_pushes_the_snapshot_over_the_budget_fails_the_run() -> None:
    length = repeat_length_filling_budget(1, over_by=1)
    run_id = insert_queued_run(f"SELECT repeat('x', {length}) AS value")

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "result_too_large"
    assert facts["returned_row_count"] is None
    assert read_snapshot(run_id) is None


def test_a_single_row_that_alone_exceeds_the_budget_fails_the_run() -> None:
    run_id = insert_queued_run(f"SELECT repeat('x', {RESULT_MAX_BYTES}) AS value")

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "result_too_large"
    assert read_snapshot(run_id) is None


def test_a_later_row_that_alone_exceeds_the_budget_fails_the_run() -> None:
    run_id = insert_queued_run(
        f"SELECT repeat('x', CASE WHEN o.id = 1 THEN 10 ELSE {RESULT_MAX_BYTES} END) AS value "
        "FROM orders o WHERE o.id IN (1, 2) ORDER BY o.id"
    )

    facts = wait_for_terminal(run_id)

    assert facts["status"] == "failed"
    assert facts["error_code"] == "result_too_large"
    assert read_snapshot(run_id) is None


def test_an_oversized_row_beyond_the_row_bound_only_marks_truncation() -> None:
    # Row 501 is wider than the whole budget, but it is only read to prove the
    # result continues, so the 500 rows already taken still stand.
    run_id = insert_queued_run(
        f"SELECT CASE WHEN o.id <= 500 THEN 'small' ELSE repeat('x', {RESULT_MAX_BYTES + 1}) END AS value "
        "FROM orders o WHERE o.id <= 501 ORDER BY o.id"
    )

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 500
    assert facts["result_truncated"] is True
    assert snapshot["result_rows"] == [["small"]] * 500


def test_multibyte_text_is_charged_as_utf8_bytes_rather_than_characters() -> None:
    run_id = insert_queued_run(
        f"SELECT repeat('数', {MULTIBYTE_CHARACTERS}) AS value "
        "FROM orders o WHERE o.id IN (1, 2, 3, 4) ORDER BY o.id"
    )

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 3
    assert facts["result_truncated"] is True
    first_cell = snapshot["result_rows"][0][0]
    assert len(first_cell) == MULTIBYTE_CHARACTERS
    assert len(first_cell.encode("utf-8")) == MULTIBYTE_CHARACTERS * 3
    assert len(snapshot_bytes(*snapshot["result_rows"])) <= RESULT_MAX_BYTES


def test_snapshot_values_keep_their_explicit_json_types() -> None:
    run_id = insert_queued_run(TYPES_SQL)

    facts = wait_for_terminal(run_id)
    snapshot = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert [column["type"] for column in snapshot["result_columns"]] == [
        "bigint",
        "character",
        "timestamp with time zone",
        "date",
        "integer",
        "numeric",
        "boolean",
        "character varying",
    ]
    (
        order_id,
        currency,
        ordered_at,
        ordered_on,
        quantity,
        unit_price,
        active,
        segment,
    ) = snapshot["result_rows"][0]
    assert isinstance(order_id, str) and order_id.isdigit()
    assert currency == "CNY"
    assert ordered_at[10] == "T" and ordered_at.endswith("+00:00")
    assert ordered_on == ordered_at[:10]
    assert isinstance(quantity, int)
    assert isinstance(unit_price, str) and "." in unit_price
    assert isinstance(active, bool)
    assert segment in {None, "enterprise", "mid_market", "small_business"}
