"""用户可调用函数的安全允许集：默认拒绝，只收录分析查询确需的函数。

判定在 AST 上按函数名进行。名字同时包含 PostgreSQL 的用户书写形式和 SQLGlot 把
同义函数折叠后的规范名（如 `to_char` 折叠为 `TIME_TO_STR`）：同一个 AST 节点类的
任一名字命中即视为允许，因为节点类本身决定了语义。

排除标准，凡属其一即不得进入允许集：

- 会执行或解释 SQL 文本：`query_to_xml`、`table_to_xml`、`dblink` 等；
- 会读取系统目录、服务器状态或配置：`version`、`current_setting`、`to_regclass`、
  `pg_get_viewdef`、`has_table_privilege`、`current_user` 等；
- 有副作用或消耗服务器资源：`set_config`、`pg_sleep`、`generate_series` 等；
- 触及文件系统或网络：`pg_read_file`、`pg_ls_dir` 等。

扩展流程见 docs/design/query-governance.md：新增函数需逐个按上述标准评估后追加。
"""

from __future__ import annotations

# 聚合
_AGGREGATE = {
    "count", "sum", "avg", "min", "max",
    "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp",
    "corr", "covar_pop", "covar_samp",
    "array_agg",
    "string_agg", "group_concat",  # group_concat 是 string_agg 的规范名
    "bool_and", "bool_or", "logical_and", "logical_or",
    "percentile_cont", "percentile_disc",
}

# 窗口
_WINDOW = {
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
    "lag", "lead", "first_value", "last_value", "nth_value",
}

# 数值
_NUMERIC = {
    "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign",
    "sqrt", "exp", "ln", "log", "power", "pow", "mod", "div",
    "greatest", "least",
}

# 字符串
_STRING = {
    "lower", "upper", "initcap",
    "length", "char_length", "character_length",
    "substring", "substr", "left", "right",
    "trim", "btrim", "ltrim", "rtrim",
    "lpad", "rpad", "pad",  # pad 是 lpad/rpad 的规范名
    "replace", "split_part", "concat", "concat_ws",
    "repeat", "reverse", "md5",
    "position", "strpos", "str_position",  # str_position 是 position/strpos 的规范名
    "regexp_replace",
}

# 日期时间
_DATETIME = {
    "date_trunc", "timestamp_trunc",  # timestamp_trunc 是 date_trunc 的规范名
    "date_part", "extract", "age", "make_date",
    "to_char", "time_to_str",  # time_to_str 是 to_char 的规范名
    "to_date", "str_to_date",  # str_to_date 是 to_date 的规范名
    "to_timestamp", "str_to_time",  # str_to_time 是 to_timestamp 的规范名
    "now", "current_date", "current_time", "current_timestamp",
}

# 条件与类型转换。case/if 由 CASE WHEN 语法生成；转换目标类型另有校验
_CONDITIONAL = {
    "coalesce", "nullif", "case", "if", "iif", "cast", "try_cast",
}

# 运算符语法在 AST 上同样落成函数节点：`a AND b`、`EXISTS (…)`、`ARRAY[…]`、
# `s ~ 'x'`、`a COLLATE "C"`。它们不是可具名调用的例程，禁掉只会打断普通 SQL，
# 并不收窄任何攻击面；其中的子查询与对象引用另由对象范围规则覆盖。
_OPERATOR = {
    "and", "or", "exists", "array", "collate", "regexp_like",
}

ALLOWED_FUNCTIONS = frozenset(
    _AGGREGATE | _WINDOW | _NUMERIC | _STRING | _DATETIME | _CONDITIONAL | _OPERATOR
)

# PostgreSQL 中不带括号的无参系统信息表达式：语法上是列引用，语义上是函数调用。
# 未加引号且未限定表名时按函数处理，加引号或带表限定的同名列不受影响。
SYSTEM_INFORMATION_KEYWORDS = frozenset(
    {"session_user", "user", "current_role", "current_catalog"}
)
