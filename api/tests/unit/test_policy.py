"""策略模块单元测试：允许、拒绝、对象范围与边界语法。

规则来源：docs/design/query-governance.md。
"""

import pytest

from app.policy import evaluate
from conftest import SYSTEM_CATALOG_BYPASSES

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
    # 词法作用域：合法的嵌套与同名 CTE 必须继续可用
    ("CTE 引用前序兄弟", (
        "WITH a AS (SELECT id FROM customers), b AS (SELECT id FROM a) "
        "SELECT count(*) FROM b"
    )),
    ("CTE 内嵌套 WITH", (
        "WITH outer_cte AS ("
        "WITH inner_cte AS (SELECT id FROM orders) SELECT count(*) AS n FROM inner_cte"
        ") SELECT n FROM outer_cte"
    )),
    ("派生表内嵌套 WITH", (
        "SELECT s.n FROM ("
        "WITH inner_cte AS (SELECT id FROM orders) SELECT count(*) AS n FROM inner_cte"
        ") AS s"
    )),
    ("递归 CTE 自引用", (
        "WITH RECURSIVE seq(n) AS ("
        "SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 5"
        ") SELECT sum(n) FROM seq"
    )),
    ("同名 CTE 体内引用物理表", (
        "WITH orders AS (SELECT id FROM orders WHERE status = 'confirmed') "
        "SELECT count(*) FROM orders"
    )),
    ("子查询引用外层 CTE", (
        "WITH recent AS (SELECT id FROM orders) "
        "SELECT id FROM customers WHERE id IN (SELECT id FROM recent)"
    )),
    # 函数允许集：常规分析函数必须继续可用
    ("聚合函数", (
        "SELECT count(*), count(DISTINCT customer_id), sum(total_amount), "
        "avg(total_amount), min(total_amount), max(total_amount) FROM orders"
    )),
    ("统计聚合", (
        "SELECT stddev(list_price), stddev_pop(list_price), stddev_samp(list_price), "
        "variance(list_price), var_pop(list_price), var_samp(list_price) FROM products"
    )),
    ("FILTER 聚合", (
        "SELECT count(*) FILTER (WHERE status = 'confirmed') FROM orders"
    )),
    ("WITHIN GROUP 聚合", (
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY list_price), "
        "percentile_disc(0.5) WITHIN GROUP (ORDER BY list_price) FROM products"
    )),
    ("集合聚合", (
        "SELECT string_agg(DISTINCT region, ','), array_agg(id) FROM customers"
    )),
    ("窗口函数集合", (
        "SELECT rank() OVER (ORDER BY list_price DESC), "
        "dense_rank() OVER (ORDER BY list_price DESC), "
        "percent_rank() OVER (ORDER BY list_price), cume_dist() OVER (ORDER BY list_price), "
        "ntile(4) OVER (ORDER BY list_price), lag(list_price) OVER (ORDER BY id), "
        "lead(list_price) OVER (ORDER BY id), first_value(sku) OVER (ORDER BY id), "
        "last_value(sku) OVER (ORDER BY id), nth_value(sku, 2) OVER (ORDER BY id) "
        "FROM products"
    )),
    ("窗口帧", (
        "SELECT sum(quantity) OVER ("
        "ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        ") FROM order_items"
    )),
    ("数值函数", (
        "SELECT abs(cost_price), ceil(list_price), floor(list_price), "
        "round(list_price, 2), sqrt(list_price), power(list_price, 2), "
        "greatest(list_price, cost_price), least(list_price, cost_price) FROM products"
    )),
    ("字符串函数", (
        "SELECT upper(region), lower(segment), initcap(display_name), "
        "length(display_name), trim(display_name), substring(customer_code, 1, 4), "
        "left(customer_code, 3), right(customer_code, 3), "
        "replace(display_name, 'a', 'b'), split_part(customer_code, '-', 2), "
        "concat(region, '-', segment), concat_ws('-', region, segment) FROM customers"
    )),
    ("日期函数", (
        "SELECT date_trunc('month', ordered_at) AS m, extract(YEAR FROM ordered_at) AS y, "
        "to_char(ordered_at, 'YYYY-MM') AS ym, count(*) FROM orders GROUP BY 1, 2, 3"
    )),
    ("当前时间函数", (
        "SELECT id FROM orders WHERE ordered_at < now() AND ordered_at >= current_date"
    )),
    ("条件与转换函数", (
        "SELECT coalesce(sku, 'N/A'), nullif(sku, ''), "
        "CASE WHEN list_price > 100 THEN 'high' ELSE 'low' END, "
        "cast(list_price AS numeric(12, 2)), list_price::text, id::bigint FROM products"
    )),
    ("运算符与谓词", (
        "SELECT c.display_name FROM customers c "
        "WHERE c.region IN ('East', 'North') AND c.segment IS NOT NULL "
        "AND c.display_name LIKE 'A%' AND c.id BETWEEN 1 AND 50 "
        "AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) "
        "ORDER BY c.customer_code || '-' || c.region LIMIT 10 OFFSET 5"
    )),
    ("区间与算术运算", (
        "SELECT count(*) FROM orders "
        "WHERE ordered_at >= now() - INTERVAL '30 days' AND total_amount * 2 > 100"
    )),
    ("表限定的同名列", (
        "SELECT c.user FROM customers c"
    )),
    ("典型业务查询", (
        "WITH confirmed AS ("
        "SELECT o.id, o.customer_id, "
        "oi.quantity * oi.unit_price * (1 - oi.discount_rate) AS amount "
        "FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.status = 'confirmed'"
        ") "
        "SELECT c.region, round(sum(confirmed.amount), 2) AS sales, "
        "rank() OVER (ORDER BY sum(confirmed.amount) DESC) AS rk "
        "FROM confirmed JOIN customers c ON c.id = confirmed.customer_id "
        "GROUP BY c.region ORDER BY sales DESC"
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
    # 越权函数：执行或解释 SQL 文本（两条已知绕过路径见 SYSTEM_CATALOG_BYPASSES）
    ("表转 XML", (
        "SELECT table_to_xml('customers', true, false, '')"
    ), "policy_forbidden_function"),
    ("限定名调用被禁函数", (
        "SELECT pg_catalog.query_to_xml('SELECT 1', true, false, '')"
    ), "policy_forbidden_function"),
    ("限定名调用允许集内函数", (
        "SELECT pg_catalog.count(*) FROM customers"
    ), "policy_forbidden_function"),
    ("引号包裹的被禁函数", (
        "SELECT \"query_to_xml\"('SELECT 1', true, false, '')"
    ), "policy_forbidden_function"),
    # 词法作用域：用词法上不可见的同名 CTE 骗过对象范围判断
    ("同名 CTE 体内引用系统目录", (
        "WITH pg_class AS (SELECT * FROM pg_class) SELECT 1 FROM pg_class"
    ), "policy_forbidden_object"),
    ("非递归 CTE 前向引用后序兄弟", (
        "WITH a AS (SELECT id FROM b), b AS (SELECT id FROM customers) SELECT * FROM a"
    ), "policy_forbidden_object"),
    ("非递归 CTE 自引用", (
        "WITH loop_cte AS (SELECT id FROM loop_cte) SELECT * FROM loop_cte"
    ), "policy_forbidden_object"),
    ("内层 CTE 名不外泄", (
        "SELECT * FROM ("
        "WITH inner_cte AS (SELECT 1 AS x) SELECT x FROM inner_cte"
        ") AS s JOIN inner_cte ON true"
    ), "policy_forbidden_object"),
    # 其余越权函数：执行 SQL 文本、读系统目录或服务器状态、产生副作用
    ("读取服务器配置", "SELECT current_setting('data_directory')", "policy_forbidden_function"),
    ("修改会话配置", (
        "SELECT set_config('search_path', 'public', false)"
    ), "policy_forbidden_function"),
    ("读取服务器文件", "SELECT pg_read_file('/etc/passwd')", "policy_forbidden_function"),
    ("列举服务器目录", "SELECT * FROM pg_ls_dir('/')", "policy_forbidden_function"),
    ("占用连接", "SELECT pg_sleep(10)", "policy_forbidden_function"),
    ("服务器版本", "SELECT version()", "policy_forbidden_function"),
    ("当前用户函数", "SELECT current_user", "policy_forbidden_function"),
    ("当前 schema 函数", "SELECT current_schema", "policy_forbidden_function"),
    ("当前库函数", "SELECT current_database()", "policy_forbidden_function"),
    ("无参系统关键字", "SELECT session_user", "policy_forbidden_function"),
    ("无参系统关键字 user", "SELECT user", "policy_forbidden_function"),
    ("对象名解析", "SELECT to_regclass('pg_class')", "policy_forbidden_function"),
    ("视图定义反查", "SELECT pg_get_viewdef('v')", "policy_forbidden_function"),
    ("权限探测", (
        "SELECT has_table_privilege('customers', 'SELECT')"
    ), "policy_forbidden_function"),
    ("XML 路径求值", "SELECT xpath('/a', '<a/>')", "policy_forbidden_function"),
    ("跨库连接函数", (
        "SELECT * FROM dblink('h', 'SELECT 1') AS t(x int)"
    ), "policy_forbidden_function"),
    ("未知函数默认拒绝", "SELECT some_unknown_udf(1)", "policy_forbidden_function"),
    ("生成序列表函数", "SELECT * FROM generate_series(1, 3)", "policy_forbidden_function"),
    ("嵌套在允许函数内的被禁函数", (
        "SELECT count(*) FROM customers WHERE region = current_setting('x')"
    ), "policy_forbidden_function"),
    # 目录对象转换：绕开表引用直接解析系统对象
    ("regclass 转换", "SELECT 'pg_class'::regclass", "policy_forbidden_feature"),
    ("oid 转换", "SELECT 1::oid", "policy_forbidden_feature"),
    ("regproc 转换", "SELECT 'sum'::regproc", "policy_forbidden_feature"),
    ("自定义类型转换", "SELECT 'x'::my_custom_type", "policy_forbidden_feature"),
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


@pytest.mark.parametrize(
    "label,sql,code",
    SYSTEM_CATALOG_BYPASSES,
    ids=[c[0] for c in SYSTEM_CATALOG_BYPASSES],
)
def test_system_catalog_bypass_rejected(label, sql, code):
    """两条已知的系统目录读取路径必须被拒绝，且不产生可执行语句。"""
    decision = evaluate(sql)
    assert not decision.allowed, f"{label} 仍被允许"
    assert decision.error_code == code, (
        f"{label} 期望 {code}，实际 {decision.error_code}"
    )
    assert decision.normalized_sql is None, "拒绝时不得产出可执行语句"


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
