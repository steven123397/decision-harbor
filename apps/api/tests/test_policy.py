from __future__ import annotations

import pytest

from decisionharbor_api.policy import evaluate_sql


def test_allows_read_only_sales_query_and_reports_objects() -> None:
    decision = evaluate_sql(
        """
        WITH sales AS (
            SELECT o.id, oi.quantity * oi.unit_price AS amount
            FROM analytics.orders AS o
            JOIN analytics.order_items AS oi ON oi.order_id = o.id
            WHERE o.status = 'confirmed'
        )
        SELECT COUNT(*) AS order_lines, SUM(amount) AS sales_amount
        FROM sales
        """
    )

    assert decision.allowed is True
    assert decision.code is None
    assert decision.referenced_objects == ("analytics.order_items", "analytics.orders")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM customers GROUP BY region",
        "SELECT region FROM analytics.customers UNION SELECT region FROM analytics.orders",
        "SELECT id, ROW_NUMBER() OVER (ORDER BY id) FROM analytics.customers",
        "SELECT CAST(id AS bigint) FROM analytics.customers LIMIT 10",
    ],
)
def test_allows_supported_read_query_shapes(sql: str) -> None:
    decision = evaluate_sql(sql)

    assert decision.allowed is True


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "sql_empty"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("UPDATE analytics.orders SET status = 'confirmed'", "non_read_query"),
        ("WITH changed AS (DELETE FROM analytics.orders RETURNING id) SELECT * FROM changed", "write_cte"),
        ("SELECT * FROM platform.query_runs", "object_not_allowed"),
        ("SELECT relname FROM pg_catalog.pg_class", "object_not_allowed"),
        ("SELECT pg_sleep(1)", "function_not_allowed"),
        ("SELECT 'orders'::regclass", "function_not_allowed"),
        ("SELECT * INTO TEMP copied_customers FROM analytics.customers", "select_into"),
        ("SELECT * FROM analytics.customers LIMIT 10001", "result_limit_exceeded"),
        ("SELECT * FROM other.customers", "object_not_allowed"),
    ],
)
def test_rejects_unsafe_sql_with_stable_code(sql: str, code: str) -> None:
    decision = evaluate_sql(sql)

    assert decision.allowed is False
    assert decision.code == code


def test_policy_limits_are_configurable_without_rewriting_sql() -> None:
    decision = evaluate_sql("SELECT * FROM analytics.customers LIMIT 5", max_result_rows=4)

    assert decision.allowed is False
    assert decision.code == "result_limit_exceeded"
