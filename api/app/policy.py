"""受治理查询的策略判定：纯函数，基于 SQLGlot AST 与对象访问范围。

规则见 docs/design/query-governance.md：默认拒绝，规则只描述允许什么。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.contract import ANALYTICS_SCHEMA, CONTRACT_TABLES

DIALECT = "postgres"

_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Alter",
    "AlterTable",
    "Drop",
    "TruncateTable",
    "Copy",
    "Command",
    "Call",
    "Grant",
    "Revoke",
    "Transaction",
    "Commit",
    "Rollback",
    "Set",
    "Use",
    "Describe",
    "Pragma",
    "LoadData",
)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node
    for name in _FORBIDDEN_NODE_NAMES
    if (node := getattr(exp, name, None)) is not None
)

_FORBIDDEN_FEATURE_NAMES = ("Into", "Lock")
FORBIDDEN_FEATURES: tuple[type[exp.Expression], ...] = tuple(
    node
    for name in _FORBIDDEN_FEATURE_NAMES
    if (node := getattr(exp, name, None)) is not None
)

_SET_OPERATIONS: tuple[type[exp.Expression], ...] = (
    exp.Union,
    exp.Intersect,
    exp.Except,
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    normalized_sql: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def _reject(code: str, message: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, error_code=code, error_message=message)


def _identifier_name(identifier: exp.Expression | None) -> str | None:
    """未加引号的标识符按 PostgreSQL 规则折叠为小写；加引号的保留原文。"""
    if identifier is None:
        return None
    if not isinstance(identifier, exp.Identifier):
        return ""
    return identifier.name if identifier.quoted else identifier.name.lower()


def _check_object_scope(root: exp.Expression) -> PolicyDecision | None:
    cte_names = {
        _identifier_name(cte.args.get("alias").this)
        for cte in root.find_all(exp.CTE)
        if isinstance(cte.args.get("alias"), exp.TableAlias)
    }
    for table in root.find_all(exp.Table):
        catalog = _identifier_name(table.args.get("catalog"))
        schema = _identifier_name(table.args.get("db"))
        name = _identifier_name(table.args.get("this"))
        if catalog:
            return _reject(
                "policy_forbidden_object",
                f"不允许访问对象 {table.sql(dialect=DIALECT)}",
            )
        if schema is None and name in cte_names:
            continue
        if schema not in (None, ANALYTICS_SCHEMA):
            return _reject(
                "policy_forbidden_object",
                f"不允许访问对象 {table.sql(dialect=DIALECT)}",
            )
        if name not in CONTRACT_TABLES:
            return _reject(
                "policy_forbidden_object",
                f"不允许访问对象 {table.sql(dialect=DIALECT)}",
            )
    return None


def evaluate(sql: str) -> PolicyDecision:
    """判定一段用户 SQL 是否允许执行。

    允许时返回归一化后的单条语句；拒绝时返回稳定错误码与可读说明。
    """
    if not sql or not sql.strip():
        return _reject("policy_parse_error", "输入为空，无法解析")

    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as error:
        return _reject("policy_parse_error", f"SQL 无法解析：{error.errors[0]['description'] if error.errors else error}")

    if not statements:
        return _reject("policy_parse_error", "输入不含可执行语句")
    if len(statements) > 1:
        return _reject("policy_multiple_statements", "只允许提交一条语句")

    root = statements[0]
    while isinstance(root, (exp.Subquery, exp.Paren)):
        inner = root.this
        if inner is None:
            return _reject("policy_parse_error", "输入不含可执行语句")
        root = inner

    if not isinstance(root, (exp.Select, *_SET_OPERATIONS)):
        return _reject(
            "policy_forbidden_statement",
            "只允许只读查询表达式（SELECT / WITH ... SELECT / 集合运算）",
        )

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject(
                "policy_forbidden_statement",
                f"语句包含被禁止的操作：{type(node).__name__.upper()}",
            )
        if isinstance(node, FORBIDDEN_FEATURES):
            return _reject(
                "policy_forbidden_feature",
                "不允许 SELECT INTO 或行锁定子句",
            )

    scope_rejection = _check_object_scope(root)
    if scope_rejection is not None:
        return scope_rejection

    return PolicyDecision(allowed=True, normalized_sql=root.sql(dialect=DIALECT))
