"""策略模块单元测试：允许、拒绝、对象范围与边界语法。

对应 docs/design/query-governance.md 的判定步骤与 docs/design/testing.md 的矩阵。
"""

import pytest

from app.policy import (
    POLICY_INVALID_SYNTAX,
    POLICY_MULTI_STATEMENT,
    POLICY_NON_QUERY_STATEMENT,
    POLICY_UNAUTHORIZED_OBJECT,
    POLICY_UNSAFE_FUNCTION,
    POLICY_WRITE_OPERATION,
    evaluate,
)

ALLOWED = [
    "SELECT id, display_name FROM customers",
    "SELECT * FROM analytics.customers",
    'SELECT * FROM "customers"',
    "SELECT * FROM CUSTOMERS",
    "SELECT region, COUNT(*) FROM customers GROUP BY region",
    "SELECT c.display_name, o.order_no FROM customers c JOIN orders o ON o.customer_id = c.id",
    "SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id)",
    "WITH x AS (SELECT * FROM orders WHERE status = 'confirmed') SELECT COUNT(*) FROM x",
    "SELECT order_no, ROW_NUMBER() OVER (ORDER BY ordered_at) FROM orders",
    "SELECT sku FROM products UNION SELECT sku FROM products",
    "SELECT sku FROM products INTERSECT SELECT sku FROM products",
    "SELECT sku FROM products EXCEPT SELECT sku FROM products",
    "SELECT i.quantity * i.unit_price * (1 - i.discount_rate) FROM order_items i",
    # CTE 名与白名单表同名：遮蔽为只读表达式，允许
    "WITH customers AS (SELECT 1 AS id) SELECT * FROM customers",
    # 合法 CTE 遮蔽系统表同名（词法作用域内是只读表达式）
    "WITH pg_tables AS (SELECT 1 AS x) SELECT * FROM pg_tables",
    # 嵌套 CTE 的词法作用域
    "WITH a AS (WITH b AS (SELECT 1 AS v) SELECT * FROM b) SELECT * FROM a",
    "WITH regional AS (SELECT region, COUNT(*) AS n FROM customers GROUP BY region) SELECT * FROM regional WHERE n > 10",
    # 安全函数允许集内的常见用法
    "SELECT COUNT(*), SUM(i.quantity), AVG(i.unit_price), MIN(i.id), MAX(i.id) FROM order_items i",
    "SELECT LOWER(p.name), UPPER(p.sku), LENGTH(p.name) FROM products p",
    "SELECT COALESCE(c.segment, 'n/a') FROM customers c",
    "SELECT DATE_TRUNC('month', o.ordered_at) FROM orders o",
    "SELECT EXTRACT(YEAR FROM o.ordered_at) FROM orders o",
    "SELECT CAST(i.unit_price AS numeric(10,2)) FROM order_items i",
    "SELECT o.order_no, LAG(o.order_no) OVER (ORDER BY o.id) FROM orders o",
    "SELECT CASE WHEN o.status = 'confirmed' THEN 1 ELSE 0 END FROM orders o",
    "SELECT c.region, STRING_AGG(c.display_name, ',') FROM customers c GROUP BY c.region",
    "SELECT ROUND(SUM(i.quantity * i.unit_price * (1 - i.discount_rate)), 2) FROM order_items i",
]

REJECTED = [
    ("SELECT", POLICY_INVALID_SYNTAX),
    ("SELECT 1; SELECT 2", POLICY_MULTI_STATEMENT),
    ("SELECT * FROM customers; DROP TABLE customers", POLICY_MULTI_STATEMENT),
    ("INSERT INTO customers (id) VALUES (1)", POLICY_NON_QUERY_STATEMENT),
    ("UPDATE customers SET region = 'East'", POLICY_NON_QUERY_STATEMENT),
    ("DELETE FROM customers", POLICY_NON_QUERY_STATEMENT),
    ("MERGE INTO customers USING products ON true WHEN MATCHED THEN DELETE", POLICY_NON_QUERY_STATEMENT),
    ("CREATE TABLE t (id int)", POLICY_NON_QUERY_STATEMENT),
    ("ALTER TABLE customers ADD COLUMN x int", POLICY_NON_QUERY_STATEMENT),
    ("DROP TABLE customers", POLICY_NON_QUERY_STATEMENT),
    ("TRUNCATE customers", POLICY_NON_QUERY_STATEMENT),
    ("COPY customers TO STDOUT", POLICY_NON_QUERY_STATEMENT),
    ("CALL do_something()", POLICY_NON_QUERY_STATEMENT),
    ("DO $$ BEGIN END $$", POLICY_NON_QUERY_STATEMENT),
    # 数据修改型 CTE 与 SELECT INTO
    ("WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x", POLICY_WRITE_OPERATION),
    ("WITH x AS (UPDATE orders SET status = 'pending' RETURNING *) SELECT * FROM x", POLICY_WRITE_OPERATION),
    ("SELECT * INTO t FROM customers", POLICY_WRITE_OPERATION),
    # 对象访问范围
    ("SELECT * FROM pg_catalog.pg_tables", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM pg_tables", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM information_schema.tables", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM other_table", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM platform.query_runs", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM public.customers", POLICY_UNAUTHORIZED_OBJECT),
    ("SELECT * FROM other_db.customers", POLICY_UNAUTHORIZED_OBJECT),
    # 子查询中的未授权对象同样拒绝
    ("SELECT * FROM orders WHERE customer_id IN (SELECT id FROM accounts)", POLICY_UNAUTHORIZED_OBJECT),
    # 缺陷回归：同名 CTE 不得让显式 pg_catalog 引用被当作遮蔽
    ("WITH pg_tables AS (SELECT 1) SELECT * FROM pg_catalog.pg_tables", POLICY_UNAUTHORIZED_OBJECT),
    ("WITH cte AS (SELECT 1) SELECT * FROM information_schema.tables", POLICY_UNAUTHORIZED_OBJECT),
    # 表函数
    ("SELECT * FROM generate_series(1, 10)", POLICY_UNAUTHORIZED_OBJECT),
    # 函数安全：未知函数、schema 限定函数、可解释 SQL 文本的函数
    ("SELECT query_to_xml('SELECT usename FROM pg_user', true, true, '')", POLICY_UNSAFE_FUNCTION),
    ("SELECT pg_sleep(1)", POLICY_UNSAFE_FUNCTION),
    ("SELECT pg_catalog.pg_sleep(1)", POLICY_UNSAFE_FUNCTION),
    ("SELECT myschema.lower('A')", POLICY_UNSAFE_FUNCTION),
    ("SELECT no_such_function(1)", POLICY_UNSAFE_FUNCTION),
    ("SELECT nextval('orders_id_seq')", POLICY_UNSAFE_FUNCTION),
    ("SELECT pg_read_file('/etc/passwd')", POLICY_UNSAFE_FUNCTION),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    decision = evaluate(sql)
    assert decision.allowed, f"should allow: {sql} (got {decision.error_code}: {decision.message})"


@pytest.mark.parametrize("sql,code", REJECTED)
def test_rejected(sql, code):
    decision = evaluate(sql)
    assert not decision.allowed, f"should reject: {sql}"
    assert decision.error_code == code
    assert decision.message
