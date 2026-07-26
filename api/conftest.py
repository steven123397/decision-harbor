"""pytest 根配置。

除了把 `api/` 置为 rootdir（使 `app` 可导入），这里集中维护两条已知的系统目录
读取绕过路径。它们同时被策略单元测试与 API 集成测试引用，SQL 只在此处保留一份，
避免两侧漂移。判定规则见 docs/design/query-governance.md。
"""

# 绕过路径一：系统 SQL 藏在函数的字符串参数里，由数据库在执行期解释
BYPASS_FUNCTION_ARGUMENT = (
    "SELECT query_to_xml("
    "'SELECT relname FROM pg_catalog.pg_class WHERE relname = ''customers''', "
    "true, false, '')"
)

# 绕过路径二：内层 CTE 与物理表同名，用词法上不可见的名字骗过对象范围判断
BYPASS_CTE_SHADOW = (
    "WITH shadow AS (WITH pg_class AS (SELECT 1 AS x) SELECT x FROM pg_class) "
    "SELECT relname, relnamespace FROM pg_class ORDER BY relname LIMIT 3"
)

# (用例名, SQL, 期望错误码)
SYSTEM_CATALOG_BYPASSES = [
    ("函数参数走私系统 SQL", BYPASS_FUNCTION_ARGUMENT, "policy_forbidden_function"),
    ("内层 CTE 遮蔽物理表", BYPASS_CTE_SHADOW, "policy_forbidden_object"),
]
