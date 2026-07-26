"""策略模块单元测试：允许、拒绝、对象范围与边界语法。

规则来源：docs/design/query-governance.md。
"""

import pytest

from app.policy import evaluate

ALLOWED_CASES = [
    ("单表查询", "SELECT id, display_name FROM customers"),
    ("无表查询", "SELECT 1 + 1"),
    ("多表连接", (
        "SELECT c.display_name, o.order_no FROM customers c "
        "JOIN orders o ON o.customer_id = c.id"
    )),
    ("契约 schema 限定", "SELECT id FROM analytics.orders"),
    ("WITH 查询", (
        "WITH confirmed AS (SELECT * FROM orders WHERE status = 'confirmed') "
        "SELECT count(*) FROM confirmed"
    )),
    ("嵌套子查询", (
        "SELECT * FROM products WHERE category_id IN "
        "(SELECT id FROM product_categories WHERE category_code = 'CAT-01')"
    )),
    ("聚合", "SELECT region, count(*) FROM customers GROUP BY region HAVING count(*) > 1"),
    ("窗口函数", (
        "SELECT order_no, row_number() OVER (PARTITION BY customer_id ORDER BY ordered_at) "
        "FROM orders"
    )),
    ("UNION", "SELECT id FROM customers UNION SELECT id FROM products"),
    ("INTERSECT", "SELECT id FROM customers INTERSECT SELECT customer_id FROM orders"),
    ("EXCEPT", "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders"),
    ("CTE 与物理表同名", (
        "WITH customers AS (SELECT 1 AS id) SELECT * FROM customers"
    )),
    ("括号包裹", "(SELECT id FROM customers)"),
    ("注释包裹", "/* 注释 */ SELECT id FROM customers -- 尾注释"),
    ("尾分号", "SELECT id FROM customers;"),
    ("大小写混合关键字", "select ID from Customers"),
    ("FROM 中的 VALUES", (
        "SELECT v.n FROM (VALUES (1), (2)) AS v(n)"
    )),
]

REJECTED_CASES = [
    ("空输入", "", "policy_parse_error"),
    ("纯空白", "   \n  ", "policy_parse_error"),
    ("不可解析", "SELECT FROM WHERE", "policy_parse_error"),
    ("多语句", "SELECT 1; SELECT 2", "policy_multiple_statements"),
    ("分号注入 DML", "SELECT 1; DELETE FROM customers", "policy_multiple_statements"),
    ("INSERT", "INSERT INTO customers (id) VALUES (1)", "policy_forbidden_statement"),
    ("UPDATE", "UPDATE customers SET region = 'East'", "policy_forbidden_statement"),
    ("DELETE", "DELETE FROM customers", "policy_forbidden_statement"),
    ("MERGE", (
        "MERGE INTO customers c USING orders o ON c.id = o.customer_id "
        "WHEN MATCHED THEN DO NOTHING"
    ), "policy_forbidden_statement"),
    ("CREATE", "CREATE TABLE t (id int)", "policy_forbidden_statement"),
    ("ALTER", "ALTER TABLE customers ADD COLUMN x int", "policy_forbidden_statement"),
    ("DROP", "DROP TABLE customers", "policy_forbidden_statement"),
    ("TRUNCATE", "TRUNCATE customers", "policy_forbidden_statement"),
    ("COPY", "COPY customers TO STDOUT", "policy_forbidden_statement"),
    ("CALL", "CALL some_procedure()", "policy_forbidden_statement"),
    ("DO", "DO $$ BEGIN NULL; END $$", "policy_forbidden_statement"),
    ("数据修改型 CTE", (
        "WITH gone AS (DELETE FROM orders RETURNING id) SELECT * FROM gone"
    ), "policy_forbidden_statement"),
    ("SELECT INTO", "SELECT id INTO saved FROM customers", "policy_forbidden_feature"),
    ("FOR UPDATE", "SELECT id FROM customers FOR UPDATE", "policy_forbidden_feature"),
    ("FOR SHARE", "SELECT id FROM customers FOR SHARE", "policy_forbidden_feature"),
    ("系统目录", "SELECT * FROM pg_catalog.pg_tables", "policy_forbidden_object"),
    ("系统目录无限定", "SELECT * FROM pg_tables", "policy_forbidden_object"),
    ("information_schema", (
        "SELECT table_name FROM information_schema.tables"
    ), "policy_forbidden_object"),
    ("未知表", "SELECT * FROM secret_stuff", "policy_forbidden_object"),
    ("其他 schema 限定", "SELECT * FROM public.customers", "policy_forbidden_object"),
    ("三段限定", "SELECT * FROM platform.public.query_runs", "policy_forbidden_object"),
    ("子查询夹带未授权对象", (
        "SELECT * FROM customers WHERE id IN (SELECT usesysid FROM pg_user)"
    ), "policy_forbidden_object"),
    ("UNION 夹带系统目录", (
        "SELECT customer_code FROM customers UNION SELECT tablename FROM pg_tables"
    ), "policy_forbidden_object"),
]


@pytest.mark.parametrize("label,sql", ALLOWED_CASES, ids=[c[0] for c in ALLOWED_CASES])
def test_allowed(label, sql):
    decision = evaluate(sql)
    assert decision.allowed, f"{label} 应被允许，实际拒绝：{decision.error_code}"
    assert decision.error_code is None
    assert decision.normalized_sql, "允许时必须输出归一化语句"


@pytest.mark.parametrize(
    "label,sql,code", REJECTED_CASES, ids=[c[0] for c in REJECTED_CASES]
)
def test_rejected(label, sql, code):
    decision = evaluate(sql)
    assert not decision.allowed, f"{label} 应被拒绝"
    assert decision.error_code == code, (
        f"{label} 期望 {code}，实际 {decision.error_code}"
    )
    assert decision.error_message, "拒绝必须携带可读说明"
    assert decision.normalized_sql is None


def test_decision_is_pure():
    sql = "SELECT id FROM customers"
    first = evaluate(sql)
    second = evaluate(sql)
    assert first.allowed == second.allowed
    assert first.normalized_sql == second.normalized_sql


def test_quoted_mixed_case_unknown_table_rejected():
    decision = evaluate('SELECT * FROM "Customers_Backup"')
    assert not decision.allowed
    assert decision.error_code == "policy_forbidden_object"
