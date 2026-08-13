from app.policy import evaluate_sql


def test_plain_select_is_allowed() -> None:
    decision = evaluate_sql("SELECT id, region FROM customers")
    assert decision.allowed is True
    assert decision.code is None


def test_with_select_is_allowed() -> None:
    sql = """
    WITH east AS (
        SELECT id FROM customers WHERE region = 'East'
    )
    SELECT id FROM east
    """
    assert evaluate_sql(sql).allowed is True


def test_join_subquery_aggregate_window_and_set_ops_are_allowed() -> None:
    join_sql = """
    SELECT c.region, p.name
    FROM orders o
    JOIN customers c ON c.id = o.customer_id
    JOIN order_items i ON i.order_id = o.id
    JOIN products p ON p.id = i.product_id
    """
    subquery_sql = """
    SELECT id FROM customers
    WHERE id IN (SELECT customer_id FROM orders WHERE status = 'confirmed')
    """
    aggregate_sql = """
    SELECT c.region, count(*) AS order_count, sum(i.quantity) AS qty
    FROM customers c
    JOIN orders o ON o.customer_id = c.id
    JOIN order_items i ON i.order_id = o.id
    GROUP BY c.region
    """
    window_sql = """
    SELECT id, row_number() OVER (PARTITION BY region ORDER BY id) AS rn
    FROM customers
    """
    set_sql = """
    SELECT id FROM customers
    UNION
    SELECT id FROM customers
    INTERSECT
    SELECT customer_id FROM orders
    EXCEPT
    SELECT customer_id FROM orders WHERE status = 'cancelled'
    """
    for sql in (join_sql, subquery_sql, aggregate_sql, window_sql, set_sql):
        decision = evaluate_sql(sql)
        assert decision.allowed is True, sql


def test_cte_name_is_not_treated_as_physical_table() -> None:
    sql = """
    WITH summary AS (
        SELECT customer_id, count(*) AS n
        FROM orders
        GROUP BY customer_id
    )
    SELECT s.n, c.display_name
    FROM summary s
    JOIN customers c ON c.id = s.customer_id
    """
    assert evaluate_sql(sql).allowed is True


def test_qualified_analytics_table_is_allowed() -> None:
    assert evaluate_sql("SELECT sku FROM analytics.products").allowed is True


def test_multiple_statements_are_invalid() -> None:
    decision = evaluate_sql("SELECT 1 FROM customers; SELECT 2 FROM products")
    assert decision.allowed is False
    assert decision.code == "QUERY_INVALID"


def test_empty_and_comment_only_are_invalid() -> None:
    for sql in ("", "   ", "-- just a comment", "/* block */"):
        decision = evaluate_sql(sql)
        assert decision.allowed is False
        assert decision.code == "QUERY_INVALID"


def test_write_statements_are_denied() -> None:
    statements = [
        "INSERT INTO customers (id, customer_code, display_name, region, created_at) VALUES (1, 'x', 'x', 'East', now())",
        "UPDATE products SET name = 'x'",
        "DELETE FROM orders",
        "MERGE INTO customers t USING customers s ON t.id = s.id WHEN MATCHED THEN UPDATE SET region = s.region",
        "CREATE TABLE foo (id int)",
        "ALTER TABLE customers ADD COLUMN extra int",
        "DROP TABLE customers",
        "TRUNCATE order_items",
        "COPY customers TO STDOUT",
        "CALL some_proc()",
        "DO $$ BEGIN PERFORM 1; END $$",
        "SELECT * INTO tmp_customers FROM customers",
        "EXPLAIN SELECT * FROM customers",
        "SHOW search_path",
        "SET statement_timeout = 1",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT * FROM customers FOR SHARE",
    ]
    for sql in statements:
        decision = evaluate_sql(sql)
        assert decision.allowed is False, sql
        assert decision.code == "POLICY_DENIED", sql


def test_modifying_cte_is_denied() -> None:
    sql = """
    WITH gone AS (
        DELETE FROM orders RETURNING id
    )
    SELECT id FROM gone
    """
    decision = evaluate_sql(sql)
    assert decision.allowed is False
    assert decision.code == "POLICY_DENIED"


def test_system_catalog_and_unauthorized_tables_are_denied() -> None:
    statements = [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM public.customers",
        "SELECT * FROM query_runs",
        "SELECT * FROM pg_stat_activity",
    ]
    for sql in statements:
        decision = evaluate_sql(sql)
        assert decision.allowed is False, sql
        assert decision.code == "POLICY_DENIED", sql


def test_disallowed_functions_are_denied() -> None:
    statements = [
        "SELECT pg_sleep(1)",
        "SELECT generate_series(1, 10)",
        "SELECT current_setting('data_directory')",
        "SELECT pg_read_file('/etc/passwd')",
    ]
    for sql in statements:
        decision = evaluate_sql(sql)
        assert decision.allowed is False, sql
        assert decision.code == "POLICY_DENIED", sql


def test_write_keywords_in_comments_or_strings_do_not_deny_select() -> None:
    sql = """
    -- INSERT INTO customers
    SELECT 'DELETE FROM orders', 'DROP TABLE products'
    FROM customers
    /* UPDATE products SET name = 'x' */
    """
    assert evaluate_sql(sql).allowed is True


def test_schema_qualified_non_catalog_function_is_denied() -> None:
    decision = evaluate_sql("SELECT public.count(*) FROM customers")
    assert decision.allowed is False
    assert decision.code == "POLICY_DENIED"
