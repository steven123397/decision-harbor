import pytest

from decisionharbor.policy import SqlPolicy


@pytest.fixture
def policy() -> SqlPolicy:
    return SqlPolicy(
        allowed_tables={
            "customers",
            "product_categories",
            "products",
            "orders",
            "order_items",
        },
    )


def test_allows_select_from_contract_table(policy: SqlPolicy) -> None:
    decision = policy.evaluate(
        "SELECT region, count(*) FROM analytics.customers GROUP BY region"
    )

    assert decision.allowed is True
    assert decision.code is None
    assert decision.referenced_objects == ("analytics.customers",)


@pytest.mark.parametrize(
    "raw_sql",
    [
        "SELECT 1",
        "SELECT * FROM analytics.customers;",
        "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT * FROM (SELECT id FROM products) AS current_products",
        "SELECT CASE WHEN active THEN cast(list_price AS numeric(12,2)) ELSE NULL END FROM products",
        "SELECT * FROM customers WHERE EXISTS (SELECT 1 FROM orders WHERE orders.customer_id = customers.id)",
        "WITH regional AS (SELECT region FROM customers) SELECT region FROM regional",
        "SELECT customer_id FROM orders UNION SELECT id FROM customers",
        "SELECT row_number() OVER (ORDER BY ordered_at) FROM orders",
        "SELECT round(avg(unit_price), 2) FROM order_items",
    ],
)
def test_allows_supported_read_only_query_shapes(policy: SqlPolicy, raw_sql: str) -> None:
    assert policy.evaluate(raw_sql).allowed is True


@pytest.mark.parametrize(
    ("raw_sql", "expected_code"),
    [
        ("", "sql_empty"),
        (" \n\t ", "sql_empty"),
        ("SELECT '" + ("x" * 65_536) + "'", "sql_too_large"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("INSERT INTO customers (id) VALUES (101)", "sql_statement_not_allowed"),
        ("WITH removed AS (DELETE FROM orders RETURNING id) SELECT * FROM removed", "sql_statement_not_allowed"),
        ("SELECT * INTO copied_customers FROM customers", "sql_statement_not_allowed"),
        ("SELECT * FROM customers FOR UPDATE", "sql_statement_not_allowed"),
        ("SELECT * FROM pg_catalog.pg_class", "sql_object_not_allowed"),
        ("SELECT * FROM platform.query_runs", "sql_object_not_allowed"),
        ("SELECT * FROM other.analytics.customers", "sql_object_not_allowed"),
        ("SELECT pg_sleep(1)", "sql_function_not_allowed"),
        ("SELECT pg_catalog.count(*) FROM customers", "sql_function_not_allowed"),
        ("SELECT * FROM generate_series(1, 2)", "sql_function_not_allowed"),
        ("WITH orders AS (SELECT 1) SELECT * FROM orders", "sql_object_not_allowed"),
        (
            "SELECT * FROM hidden WHERE EXISTS (WITH hidden AS (SELECT 1) SELECT * FROM hidden)",
            "sql_object_not_allowed",
        ),
        ("VALUES (1)", "sql_statement_not_allowed"),
        ("EXPLAIN SELECT 1", "sql_statement_not_allowed"),
        ("SHOW search_path", "sql_statement_not_allowed"),
        ("SELECT * FROM customers TABLESAMPLE SYSTEM (10)", "unsupported_sql"),
    ],
)
def test_rejects_queries_outside_the_governed_subset(
    policy: SqlPolicy,
    raw_sql: str,
    expected_code: str,
) -> None:
    decision = policy.evaluate(raw_sql)

    assert decision.allowed is False
    assert decision.code == expected_code


def test_rejects_invalid_sql(policy: SqlPolicy) -> None:
    decision = policy.evaluate("SELECT FROM")

    assert decision.allowed is False
    assert decision.code == "sql_parse_error"
