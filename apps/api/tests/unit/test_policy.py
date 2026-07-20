"""SqlPolicy unit tests — no database required."""

from __future__ import annotations

import pytest

from app.policy import ALLOWED_TABLES, PolicyDecision, SqlPolicy


@pytest.fixture
def policy() -> SqlPolicy:
    return SqlPolicy()


def test_allows_simple_select(policy: SqlPolicy) -> None:
    result = policy.check("SELECT id, display_name FROM customers LIMIT 5")
    assert result.decision is PolicyDecision.ALLOW
    assert result.error_code is None


def test_allows_join_and_aggregation(policy: SqlPolicy) -> None:
    sql = """
    SELECT c.region, COUNT(*) AS order_count
    FROM customers c
    JOIN orders o ON o.customer_id = c.id
    WHERE o.status = 'confirmed'
    GROUP BY c.region
    """
    assert policy.check(sql).decision is PolicyDecision.ALLOW


def test_allows_with_select_and_window(policy: SqlPolicy) -> None:
    sql = """
    WITH ranked AS (
      SELECT id, quantity,
             ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY quantity DESC) AS rn
      FROM order_items
    )
    SELECT * FROM ranked WHERE rn = 1
    """
    assert policy.check(sql).decision is PolicyDecision.ALLOW


def test_allows_union(policy: SqlPolicy) -> None:
    sql = """
    SELECT id FROM customers
    UNION
    SELECT id FROM products
    """
    assert policy.check(sql).decision is PolicyDecision.ALLOW


def test_rejects_multi_statement(policy: SqlPolicy) -> None:
    result = policy.check("SELECT 1; SELECT 2")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_MULTI_STATEMENT"


def test_rejects_insert(policy: SqlPolicy) -> None:
    result = policy.check("INSERT INTO customers (id, customer_code, display_name, region, created_at) VALUES (1,'x','y','East', NOW())")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_STATEMENT"


def test_rejects_update_delete_drop(policy: SqlPolicy) -> None:
    for sql in (
        "UPDATE products SET active = false WHERE id = 1",
        "DELETE FROM orders WHERE id = 1",
        "DROP TABLE customers",
    ):
        result = policy.check(sql)
        assert result.decision is PolicyDecision.REJECT
        assert result.error_code == "POLICY_FORBIDDEN_STATEMENT", sql


def test_rejects_unauthorized_table(policy: SqlPolicy) -> None:
    result = policy.check("SELECT * FROM secrets")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_OBJECT"


def test_rejects_system_catalog(policy: SqlPolicy) -> None:
    result = policy.check("SELECT * FROM pg_catalog.pg_tables")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_OBJECT"


def test_rejects_information_schema(policy: SqlPolicy) -> None:
    result = policy.check("SELECT * FROM information_schema.tables")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_OBJECT"


def test_rejects_non_public_schema(policy: SqlPolicy) -> None:
    result = policy.check("SELECT * FROM other.customers")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_OBJECT"


def test_allows_public_schema_qualified_allowed_table(policy: SqlPolicy) -> None:
    result = policy.check("SELECT id FROM public.customers")
    assert result.decision is PolicyDecision.ALLOW


def test_rejects_parse_error(policy: SqlPolicy) -> None:
    result = policy.check("SELEECT FROM")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_PARSE_ERROR"


def test_rejects_empty_and_whitespace(policy: SqlPolicy) -> None:
    for sql in ("", "   ", "\n\t"):
        result = policy.check(sql)
        assert result.decision is PolicyDecision.REJECT
        assert result.error_code in {"POLICY_PARSE_ERROR", "POLICY_FORBIDDEN_STATEMENT"}


def test_rejects_sql_too_large(policy: SqlPolicy) -> None:
    huge = "SELECT id FROM customers --" + ("x" * (100 * 1024))
    result = policy.check(huge)
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_SQL_TOO_LARGE"


def test_rejects_select_into(policy: SqlPolicy) -> None:
    result = policy.check("SELECT id INTO TEMP tmp_customers FROM customers")
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code in {"POLICY_FORBIDDEN_STATEMENT", "POLICY_PARSE_ERROR"}


def test_rejects_modifying_cte(policy: SqlPolicy) -> None:
    sql = """
    WITH moved AS (
      DELETE FROM order_items WHERE quantity < 0 RETURNING id
    )
    SELECT * FROM moved
    """
    result = policy.check(sql)
    assert result.decision is PolicyDecision.REJECT
    assert result.error_code == "POLICY_FORBIDDEN_STATEMENT"


def test_allowed_tables_match_contract() -> None:
    assert ALLOWED_TABLES == {
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
    }
