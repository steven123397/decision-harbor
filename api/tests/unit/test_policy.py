import pytest

from app.policy import check_policy


class TestAllowedQueries:
    def test_simple_select(self):
        v = check_policy("SELECT * FROM customers")
        assert v.allowed

    def test_select_with_join(self):
        v = check_policy(
            "SELECT o.order_no, c.display_name FROM orders o JOIN customers c ON o.customer_id = c.id"
        )
        assert v.allowed

    def test_select_with_subquery(self):
        v = check_policy(
            "SELECT * FROM customers WHERE id IN (SELECT customer_id FROM orders)"
        )
        assert v.allowed

    def test_select_with_cte(self):
        v = check_policy(
            "WITH confirmed AS (SELECT * FROM orders WHERE status = 'confirmed') SELECT * FROM confirmed"
        )
        assert v.allowed

    def test_select_with_window_function(self):
        v = check_policy(
            "SELECT id, ROW_NUMBER() OVER (PARTITION BY region ORDER BY id) FROM customers"
        )
        assert v.allowed

    def test_select_with_union(self):
        v = check_policy(
            "SELECT id FROM customers UNION SELECT id FROM products"
        )
        assert v.allowed

    def test_select_with_aggregation(self):
        v = check_policy(
            "SELECT region, COUNT(*) FROM customers GROUP BY region HAVING COUNT(*) > 1"
        )
        assert v.allowed

    def test_all_five_tables(self):
        v = check_policy(
            "SELECT oi.id FROM order_items oi "
            "JOIN orders o ON oi.order_id = o.id "
            "JOIN customers c ON o.customer_id = c.id "
            "JOIN products p ON oi.product_id = p.id "
            "JOIN product_categories pc ON p.category_id = pc.id"
        )
        assert v.allowed

    def test_case_insensitive_table(self):
        v = check_policy("SELECT * FROM CUSTOMERS")
        assert v.allowed

    def test_quoted_identifier(self):
        v = check_policy('SELECT * FROM "customers"')
        assert v.allowed

    def test_select_with_limit_within_max(self):
        v = check_policy("SELECT * FROM customers LIMIT 10", max_rows=1000)
        assert v.allowed
        assert "LIMIT 10" in v.parsed_sql.upper() or "LIMIT\n  10" in v.parsed_sql.upper() or "10" in v.parsed_sql


class TestRejectedQueries:
    def test_multi_statement(self):
        v = check_policy("SELECT 1; SELECT 2")
        assert not v.allowed
        assert v.code == "MULTI_STATEMENT"

    def test_insert(self):
        v = check_policy("INSERT INTO customers (id) VALUES (1)")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_update(self):
        v = check_policy("UPDATE customers SET region = 'X'")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_delete(self):
        v = check_policy("DELETE FROM customers")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_drop(self):
        v = check_policy("DROP TABLE customers")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_create(self):
        v = check_policy("CREATE TABLE evil (id int)")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_truncate(self):
        v = check_policy("TRUNCATE customers")
        assert not v.allowed
        assert v.code == "FORBIDDEN_STATEMENT"

    def test_data_modifying_cte(self):
        v = check_policy(
            "WITH deleted AS (DELETE FROM customers RETURNING *) SELECT * FROM deleted"
        )
        assert not v.allowed
        assert v.code in ("FORBIDDEN_CTE", "FORBIDDEN_STATEMENT")

    def test_select_into(self):
        v = check_policy("SELECT * INTO new_table FROM customers")
        assert not v.allowed
        assert v.code == "FORBIDDEN_INTO"

    def test_forbidden_table(self):
        v = check_policy("SELECT * FROM secret_table")
        assert not v.allowed
        assert v.code == "FORBIDDEN_OBJECT"

    def test_pg_catalog(self):
        v = check_policy("SELECT * FROM pg_catalog.pg_tables")
        assert not v.allowed
        assert v.code == "FORBIDDEN_OBJECT"

    def test_information_schema(self):
        v = check_policy("SELECT * FROM information_schema.tables")
        assert not v.allowed
        assert v.code == "FORBIDDEN_OBJECT"

    def test_parse_error(self):
        v = check_policy("SELEC BROKEN !!!")
        assert not v.allowed
        assert v.code == "PARSE_ERROR"

    def test_empty_sql(self):
        v = check_policy("")
        assert not v.allowed
        assert v.code == "PARSE_ERROR"

    def test_comment_only(self):
        v = check_policy("-- just a comment")
        assert not v.allowed
        assert v.code == "PARSE_ERROR"


class TestLimitEnforcement:
    def test_no_limit_gets_max(self):
        v = check_policy("SELECT * FROM customers", max_rows=1000)
        assert v.allowed
        assert "1000" in v.parsed_sql

    def test_limit_above_max_tightened(self):
        v = check_policy("SELECT * FROM customers LIMIT 5000", max_rows=1000)
        assert v.allowed
        assert "5000" not in v.parsed_sql
        assert "1000" in v.parsed_sql

    def test_limit_within_max_preserved(self):
        v = check_policy("SELECT * FROM customers LIMIT 50", max_rows=1000)
        assert v.allowed
        assert "50" in v.parsed_sql
