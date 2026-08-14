from app.policy import (
    check,
    DEFAULT_ALLOWED_TABLES,
    POLICY_DATA_MODIFYING_CTE,
    POLICY_EMPTY_STATEMENT,
    POLICY_FORBIDDEN_STATEMENT,
    POLICY_MULTIPLE_STATEMENTS,
    POLICY_NON_SELECT,
    POLICY_PARSE_ERROR,
    POLICY_SELECT_INTO,
    POLICY_UNAUTHORIZED_OBJECT,
)


def allowed(sql):
    return check(sql).allowed


def code_of(sql):
    return check(sql).code


class TestAllowed:
    def test_literal(self):
        assert allowed("SELECT 1")

    def test_simple_table(self):
        assert allowed("SELECT * FROM orders")

    def test_explicit_schema(self):
        assert allowed("SELECT * FROM analytics.orders")

    def test_filter(self):
        assert allowed("SELECT id, display_name FROM customers WHERE region = 'East'")

    def test_join(self):
        assert allowed(
            "SELECT o.id, c.display_name FROM orders o JOIN customers c ON c.id = o.customer_id"
        )

    def test_subquery(self):
        assert allowed("SELECT * FROM (SELECT id FROM products) p")

    def test_aggregation(self):
        assert allowed("SELECT count(*) FROM order_items")

    def test_window(self):
        assert allowed("SELECT id, row_number() OVER (ORDER BY id) FROM customers")

    def test_union(self):
        assert allowed("SELECT id FROM products UNION SELECT id FROM orders")

    def test_with_cte(self):
        assert allowed("WITH x AS (SELECT * FROM orders) SELECT * FROM x")

    def test_with_cte_select(self):
        assert allowed("WITH recent AS (SELECT id FROM orders) SELECT count(*) FROM recent")

    def test_keyword_in_string(self):
        assert allowed("SELECT 'DROP TABLE' AS literal")

    def test_keyword_in_comment(self):
        assert allowed("SELECT 1 -- INSERT INTO orders")


class TestRejected:
    def test_empty(self):
        assert code_of("") == POLICY_EMPTY_STATEMENT

    def test_whitespace(self):
        assert code_of("   ") == POLICY_EMPTY_STATEMENT

    def test_comment_only(self):
        assert code_of("-- just a comment") == POLICY_EMPTY_STATEMENT

    def test_multiple_statements(self):
        assert code_of("SELECT 1; SELECT 2") == POLICY_MULTIPLE_STATEMENTS

    def test_insert(self):
        assert code_of("INSERT INTO orders VALUES (1)") == POLICY_FORBIDDEN_STATEMENT

    def test_update(self):
        assert code_of("UPDATE orders SET status = 'x'") == POLICY_FORBIDDEN_STATEMENT

    def test_delete(self):
        assert code_of("DELETE FROM orders") == POLICY_FORBIDDEN_STATEMENT

    def test_create(self):
        assert code_of("CREATE TABLE t (id int)") == POLICY_FORBIDDEN_STATEMENT

    def test_alter(self):
        assert code_of("ALTER TABLE products ADD COLUMN x int") == POLICY_FORBIDDEN_STATEMENT

    def test_drop(self):
        assert code_of("DROP TABLE products") == POLICY_FORBIDDEN_STATEMENT

    def test_truncate(self):
        assert code_of("TRUNCATE TABLE products") == POLICY_FORBIDDEN_STATEMENT

    def test_copy(self):
        assert code_of("COPY orders TO STDOUT") == POLICY_FORBIDDEN_STATEMENT

    def test_call(self):
        assert code_of("CALL foo()") == POLICY_FORBIDDEN_STATEMENT

    def test_select_into(self):
        assert code_of("SELECT * INTO newt FROM orders") == POLICY_SELECT_INTO

    def test_data_modifying_cte(self):
        sql = "WITH x AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT * FROM x"
        assert code_of(sql) == POLICY_DATA_MODIFYING_CTE

    def test_unauthorized_table(self):
        assert code_of("SELECT * FROM secret") == POLICY_UNAUTHORIZED_OBJECT

    def test_system_catalog(self):
        assert code_of("SELECT * FROM pg_catalog.pg_class") == POLICY_UNAUTHORIZED_OBJECT

    def test_information_schema(self):
        assert code_of("SELECT * FROM information_schema.tables") == POLICY_UNAUTHORIZED_OBJECT

    def test_other_schema(self):
        assert code_of("SELECT * FROM platform.query_runs") == POLICY_UNAUTHORIZED_OBJECT

    def test_cross_database(self):
        assert code_of("SELECT * FROM other.analytics.orders") == POLICY_UNAUTHORIZED_OBJECT

    def test_non_select_statement(self):
        assert code_of("VALUES (1, 2)") == POLICY_NON_SELECT


class TestAllowlistDriven:
    def test_custom_allowlist_allows(self):
        decision = check("SELECT * FROM secret", allowed_tables=frozenset({"secret"}))
        assert decision.allowed

    def test_default_allowlist_matches_contract(self):
        from app.schema_catalog import load_catalog
        from app.config import settings

        catalog = load_catalog(settings.dataset_dir + "/contract.json")
        assert catalog.table_names == DEFAULT_ALLOWED_TABLES
