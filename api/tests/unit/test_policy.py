"""策略模块单元测试。

判定规则与拒绝码以 docs/design/query-governance.md 为准。
allowed_tables 模拟由 contract.json 派生的五张契约表。
"""

import pytest

from app.policy import PolicyDecision, PolicyLimits, evaluate

ALLOWED_TABLES = frozenset(
    {"customers", "product_categories", "products", "orders", "order_items"}
)


def decide(sql: str) -> PolicyDecision:
    return evaluate(sql, allowed_tables=ALLOWED_TABLES)


# ---------------------------------------------------------------- 允许


@pytest.mark.parametrize(
    "sql",
    [
        # 基本查询
        "SELECT * FROM customers",
        # 连接与别名
        "SELECT c.region, count(*) AS n FROM customers c "
        "JOIN orders o ON o.customer_id = c.id GROUP BY 1 ORDER BY n DESC",
        # 子查询
        "SELECT * FROM products WHERE category_id IN "
        "(SELECT id FROM product_categories WHERE name LIKE 'A%')",
        # 聚合 + 窗口函数
        "SELECT region, sum(quantity) OVER (PARTITION BY region) FROM order_items",
        # WITH ... SELECT 与 CTE 自引用
        "WITH t AS (SELECT customer_id FROM orders WHERE status = 'confirmed') "
        "SELECT count(*) FROM t JOIN customers ON t.customer_id = customers.id",
        # 集合操作
        "SELECT id FROM customers UNION SELECT customer_id FROM orders",
        "SELECT id FROM customers INTERSECT SELECT customer_id FROM orders",
        "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders",
        # 显式 analytics schema 限定
        "SELECT * FROM analytics.customers",
        # 尾部分号
        "SELECT count(*) FROM orders;",
        # 注释与大小写
        "-- top regions\nselect region /* inline */ from customers",
        # CASE 表达式、DISTINCT、ILIKE 与窗口帧
        "SELECT CASE WHEN quantity > 1 THEN 'multi' ELSE 'single' END FROM order_items",
        "SELECT DISTINCT region FROM customers WHERE region ILIKE 'e%'",
        "SELECT sum(quantity) OVER (ORDER BY quantity "
        "ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) FROM order_items",
        # 递归 CTE 自引用在词法作用域内解析为 CTE 定义
        "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
        "SELECT * FROM r",
        # 常量查询
        "SELECT 1",
    ],
)
def test_allows_readonly_queries(sql):
    assert decide(sql).allowed is True


# ---------------------------------------------------------------- 拒绝：解析与结构


def test_rejects_empty_sql():
    d = decide("")
    assert (d.allowed, d.code) == (False, "QY_INVALID_SYNTAX")


def test_rejects_comment_only_sql():
    d = decide("-- nothing executable")
    assert (d.allowed, d.code) == (False, "QY_INVALID_SYNTAX")


def test_rejects_invalid_syntax():
    d = decide("SELECT FROM WHERE")
    assert (d.allowed, d.code) == (False, "QY_INVALID_SYNTAX")


def test_rejects_multiple_statements():
    d = decide("SELECT 1; DELETE FROM customers")
    assert (d.allowed, d.code) == (False, "QY_MULTIPLE_STATEMENTS")


def test_rejects_overlong_sql():
    d = evaluate(
        "SELECT * FROM customers WHERE id = 1 AND " + "x" * PolicyLimits().sql_max_length,
        allowed_tables=ALLOWED_TABLES,
    )
    assert (d.allowed, d.code) == (False, "QY_SQL_TOO_LONG")


@pytest.mark.parametrize("sql", ["SELECT $1", "SELECT ?", "SELECT :name", "SELECT %s"])
def test_rejects_placeholders(sql):
    assert (decide(sql).code, ) == ("QY_INVALID_SYNTAX",)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers VALUES (1, 'X', 'X', 'East', NULL, now())",
        "UPDATE customers SET region = 'West'",
        "DELETE FROM customers",
        "MERGE INTO customers USING products ON 1=1 "
        "WHEN MATCHED THEN UPDATE SET region = 'West'",
        "CREATE TABLE hack (id int)",
        "ALTER TABLE customers ADD COLUMN extra int",
        "DROP TABLE customers",
        "TRUNCATE TABLE customers",
        "COPY customers FROM STDIN",
        "CALL some_procedure()",
        "DO $$ BEGIN END $$",
        "GRANT SELECT ON customers TO public",
        "SET statement_timeout = 0",
        "VACUUM customers",
        # 行锁子句：只读查询表达式也不允许携带锁
        "SELECT * FROM customers FOR UPDATE",
        "SELECT * FROM customers WHERE id = 1 FOR SHARE",
        "SELECT * FROM customers WHERE id = 1 FOR NO KEY UPDATE",
    ],
)
def test_rejects_forbidden_statements(sql):
    d = decide(sql)
    assert (d.allowed, d.code) == (False, "QY_FORBIDDEN_STATEMENT")


def test_rejects_select_into():
    d = decide("SELECT * INTO dump_table FROM customers")
    assert (d.allowed, d.code) == (False, "QY_SELECT_INTO")


def test_rejects_data_modifying_cte():
    d = decide(
        "WITH t AS (INSERT INTO customers VALUES (1) RETURNING *) SELECT * FROM t"
    )
    assert (d.allowed, d.code) == (False, "QY_WRITE_CTE")


# ---------------------------------------------------------------- 拒绝：对象范围


@pytest.mark.parametrize(
    "sql",
    [
        # 其他数据库（platform 审计表）
        "SELECT * FROM platform.query_runs",
        "SELECT * FROM postgres.pg_catalog.pg_tables",
        # 系统目录与 information schema
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        # 未知裸表名（不在契约白名单内）
        "SELECT * FROM query_runs",
        # 未知 schema 限定
        "SELECT * FROM public.customers",
        # CTE 遮蔽系统目录：非递归 CTE 体内自引用回退到 pg_catalog 真表
        "WITH pg_class AS (SELECT relname FROM pg_class) SELECT * FROM pg_class",
        # 作用域外借用 CTE 名访问裸系统表
        "SELECT * FROM (WITH t AS (SELECT 1 AS a) SELECT a FROM t) s "
        "JOIN pg_class ON true",
        # CTE 名与授权表同名（遮蔽语义歧义，直接拒绝）
        "WITH customers AS (SELECT 1 AS x) SELECT * FROM customers",
    ],
)
def test_rejects_unauthorized_objects(sql):
    d = decide(sql)
    assert (d.allowed, d.code) == (False, "QY_UNAUTHORIZED_OBJECT")


# ---------------------------------------------------------------- 拒绝：类型转换


@pytest.mark.parametrize(
    "sql",
    [
        # 对象标识类型转换是系统目录访问入口
        "SELECT 'pg_catalog.pg_class'::regclass",
        "SELECT 'orders'::regclass",
        "SELECT CAST('1' AS oid)",
        "SELECT 'x'::int[]",
        "SELECT 'x'::\"regclass\"",
        # 枚举/自定义类型
        "SELECT CAST('x' AS some_enum)",
    ],
)
def test_rejects_unsafe_casts(sql):
    d = decide(sql)
    assert (d.allowed, d.code) == (False, "QY_UNAUTHORIZED_OBJECT")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'x'::varchar(99999999)",
        "SELECT 'x'::numeric(-1)",
        "SELECT 'x'::numeric(1001)",
        "SELECT 'x'::numeric(5, 6)",
        "SELECT 'x'::timestamp(9)",
        "SELECT 'x'::text(3)",
    ],
)
def test_rejects_out_of_range_cast_parameters(sql):
    d = decide(sql)
    assert (d.allowed, d.code) == (False, "QY_INVALID_SYNTAX")


def test_allows_safe_casts():
    assert decide(
        "SELECT 'x'::text, CAST('1' AS integer), 'a'::varchar(50), "
        "'1.5'::numeric(12, 2), quantity::bigint, ordered_at::date FROM order_items"
    ).allowed


# ---------------------------------------------------------------- 拒绝：函数与节点


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(3)",
        "SELECT dblink('host=x', 'SELECT 1')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(0, '/tmp/x')",
        "SELECT dblink_exec('host=x', 'DELETE FROM customers')",
        "SELECT * FROM customers WHERE id = 1 OR pg_sleep(10)::text = 'x'",
        # 系统信息与逃逸路径：白名单外一律拒绝
        "SELECT query_to_xml('SELECT relname FROM pg_class', true, true, '')",
        "SELECT current_setting('search_path')",
        "SELECT current_user",
        "SELECT session_user",
        "SELECT version()",
        "SELECT inet_client_addr()",
    ],
)
def test_rejects_forbidden_functions(sql):
    d = decide(sql)
    assert (d.allowed, d.code) == (False, "QY_FORBIDDEN_FUNCTION")


def test_allows_normal_functions():
    assert decide("SELECT lower(region), coalesce(segment, 'none') FROM customers").allowed


def test_rejects_unsupported_nodes():
    # 结构在支持节点白名单之外（管道函数调用形态）
    d = decide("SELECT to_jsonb(customers) FROM customers")
    assert (d.allowed, d.code) == (False, "QY_FORBIDDEN_FUNCTION")
