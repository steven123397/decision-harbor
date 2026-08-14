"""SQL 治理策略：基于 SQLGlot AST 与对象访问范围的纯函数判定。

判定顺序与拒绝码语义见 docs/design/query-governance.md。
本包不访问数据库、不读配置文件；限额与授权表由调用方传入。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

QY_SQL_TOO_LONG = "QY_SQL_TOO_LONG"
QY_INVALID_SYNTAX = "QY_INVALID_SYNTAX"
QY_MULTIPLE_STATEMENTS = "QY_MULTIPLE_STATEMENTS"
QY_FORBIDDEN_STATEMENT = "QY_FORBIDDEN_STATEMENT"
QY_SELECT_INTO = "QY_SELECT_INTO"
QY_WRITE_CTE = "QY_WRITE_CTE"
QY_UNAUTHORIZED_OBJECT = "QY_UNAUTHORIZED_OBJECT"
QY_FORBIDDEN_FUNCTION = "QY_FORBIDDEN_FUNCTION"

# 数据库与 schema 名固定为 analytics；用户 SQL 只允许访问该范围。
ANALYTICS_DB = "analytics"

# 根节点白名单：查询表达式。
_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# 全树节点黑名单：写操作与命令类语句（嵌套出现也拒绝）。
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Alter,
    exp.Drop,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Grant,
    exp.Set,
)

# 已知逃逸路径函数黑名单；角色级 statement_timeout 是兜底防线。
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "PG_READ_FILE",
        "PG_READ_BINARY_FILE",
        "LO_IMPORT",
        "LO_EXPORT",
        "DBLINK",
        "DBLINK_EXEC",
        "PG_SLEEP",
        "PG_ADVISORY_LOCK",
        "PG_ADVISORY_UNLOCK",
    }
)


@dataclass(frozen=True)
class PolicyLimits:
    sql_max_length: int = 100_000


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None

    @classmethod
    def pass_(cls) -> "PolicyDecision":
        return cls(True)

    @classmethod
    def reject(cls, code: str, message: str) -> "PolicyDecision":
        return cls(False, code, message)


def evaluate(
    sql: str,
    *,
    limits: PolicyLimits | None = None,
    allowed_tables: frozenset[str] = frozenset(),
) -> PolicyDecision:
    """判定一条用户 SQL 是否允许执行；任何一步失败即返回对应拒绝码。"""
    limits = limits or PolicyLimits()

    if not isinstance(sql, str) or len(sql) > limits.sql_max_length:
        return PolicyDecision.reject(QY_SQL_TOO_LONG, "查询超过允许的最大长度")

    if not sql.strip():
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询不能为空")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询无法解析为合法的 SQL")

    statements = [s for s in statements if s is not None]
    if not statements:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询不能为空")
    if len(statements) != 1:
        return PolicyDecision.reject(QY_MULTIPLE_STATEMENTS, "仅允许单条查询语句")

    root = statements[0]
    if not isinstance(root, _QUERY_ROOTS):
        return PolicyDecision.reject(QY_FORBIDDEN_STATEMENT, "仅允许只读查询语句")

    if root.find(exp.Placeholder, exp.Parameter) is not None:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "不支持绑定参数占位符")

    if root.find(exp.Into) is not None:
        return PolicyDecision.reject(QY_SELECT_INTO, "不允许 SELECT INTO")

    # 数据修改型 CTE 优先于通用节点黑名单判定，保证稳定拒绝码。
    for cte in root.find_all(exp.CTE):
        if not isinstance(cte.this, _QUERY_ROOTS):
            return PolicyDecision.reject(QY_WRITE_CTE, "WITH 子句中不允许数据修改语句")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return PolicyDecision.reject(QY_FORBIDDEN_STATEMENT, "仅允许只读查询语句")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        catalog = (table.catalog or "").lower()
        schema = (table.db or "").lower()
        name = table.name.lower()
        if catalog and catalog != ANALYTICS_DB:
            return _unauthorized()
        if schema:
            if schema != ANALYTICS_DB:
                return _unauthorized()
        elif name not in allowed_tables and name not in cte_names:
            return _unauthorized()

    for func in root.find_all(exp.Func):
        fname = func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
        if fname in _FORBIDDEN_FUNCTIONS:
            return PolicyDecision.reject(QY_FORBIDDEN_FUNCTION, "查询使用了不允许的函数")

    return PolicyDecision.pass_()


def _unauthorized() -> PolicyDecision:
    return PolicyDecision.reject(
        QY_UNAUTHORIZED_OBJECT, "仅允许访问 analytics 库的销售分析表"
    )
