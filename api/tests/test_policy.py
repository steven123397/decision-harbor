"""SQL 策略单元测试：纯函数，无数据库。覆盖允许、拒绝、对象范围与边界语法。"""
import pytest

from app.policy import analyze


# ---- 允许 ----
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, customer_code FROM analytics.customers LIMIT 5",
        "SELECT * FROM customers",
        "SELECT 1",
        "SELECT now()",
        "WITH cte AS (SELECT * FROM analytics.orders) SELECT * FROM cte",
        "SELECT c.display_name, o.order_no FROM analytics.customers c "
        "JOIN analytics.orders o ON o.customer_id = c.id LIMIT 1",
        "SELECT * FROM analytics.orders WHERE customer_id IN (SELECT id FROM analytics.customers)",
        "SELECT customer_id, count(*) FROM analytics.orders GROUP BY customer_id",
        "SELECT customer_id, row_number() OVER (PARTITION BY customer_id ORDER BY id) "
        "FROM analytics.order_items",
        "SELECT count(*) FROM analytics.customers "
        "UNION SELECT count(*) FROM analytics.orders",
        "SELECT count(*) FROM analytics.customers "
        "INTERSECT SELECT count(*) FROM analytics.customers",
        "SELECT count(*) FROM analytics.orders "
        "EXCEPT SELECT count(*) FROM analytics.orders",
        "SELECT * FROM analytics.product_categories",
        'SELECT * FROM "analytics"."customers" LIMIT 1',
    ],
)
def test_allowed(sql):
    result = analyze(sql)
    assert result.allowed, f"expected allowed, got violations={result.violations} for: {sql}"


# ---- 拒绝：多语句 ----
def test_reject_multi_statement():
    assert analyze("SELECT 1; SELECT 2;").violations == ["MULTI_STATEMENT"]


def test_reject_empty():
    assert analyze("").violations == ["PARSE_ERROR"]
    assert analyze("   ").violations == ["PARSE_ERROR"]


# ---- 拒绝：禁止语句类型 ----
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO analytics.customers (id) VALUES (1)",
        "UPDATE analytics.customers SET display_name='x' WHERE id=1",
        "DELETE FROM analytics.customers",
        "MERGE INTO analytics.customers USING analytics.orders ON true WHEN MATCHED THEN DELETE",
        "CREATE TABLE analytics.x (id int)",
        "ALTER TABLE analytics.customers DROP COLUMN region",
        "DROP TABLE analytics.customers",
        "TRUNCATE analytics.customers",
        "SET statement_timeout = 1",
        "VACUUM analytics.customers",
    ],
)
def test_reject_forbidden_statement(sql):
    result = analyze(sql)
    assert not result.allowed
    assert result.violations[0] == "FORBIDDEN_STATEMENT"


# ---- 拒绝：COPY / CALL / DO（无法解析为只读表达式，fail-closed）----
@pytest.mark.parametrize(
    "sql",
    [
        "COPY analytics.customers TO '/tmp/x'",
        "CALL some_proc()",
        "DO $$ BEGIN PERFORM 1; END $$",
    ],
)
def test_reject_non_read_expression(sql):
    result = analyze(sql)
    assert not result.allowed


# ---- 拒绝：数据修改型 CTE ----
def test_reject_data_modifying_cte():
    sql = (
        "WITH d AS (DELETE FROM analytics.customers RETURNING *) "
        "SELECT * FROM d"
    )
    result = analyze(sql)
    assert not result.allowed
    assert "DATA_MODIFYING_CTE" in result.violations


# ---- 拒绝：SELECT INTO ----
def test_reject_select_into():
    result = analyze("SELECT * INTO analytics.new_table FROM analytics.customers")
    assert not result.allowed
    assert "SELECT_INTO" in result.violations


# ---- 拒绝：对象越界 ----
def test_reject_non_analytics_schema():
    result = analyze("SELECT * FROM public.customers")
    assert not result.allowed
    assert "FORBIDDEN_OBJECT" in result.violations


def test_reject_unknown_table():
    result = analyze("SELECT * FROM analytics.nonexistent")
    assert not result.allowed
    assert "FORBIDDEN_OBJECT" in result.violations


def test_reject_system_catalog():
    assert not analyze("SELECT * FROM pg_catalog.pg_class").allowed
    assert not analyze("SELECT * FROM information_schema.tables").allowed


def test_reject_unparseable():
    assert not analyze("SELECT FROM WHERE").allowed
    assert analyze("SELECT FROM WHERE").violations[0] == "PARSE_ERROR"


# ---- 对象范围记录 ----
def test_object_scope_recorded():
    result = analyze(
        "SELECT * FROM analytics.customers c JOIN analytics.orders o ON o.customer_id=c.id"
    )
    assert ("analytics", "customers") in result.object_scope
    assert ("analytics", "orders") in result.object_scope
