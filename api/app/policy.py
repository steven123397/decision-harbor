"""受治理查询的策略判定：纯函数，基于 SQLGlot AST 与对象访问范围。

规则见 docs/design/query-governance.md：默认拒绝，规则只描述允许什么。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.contract import ANALYTICS_SCHEMA, CONTRACT_TABLES
from app.sql_functions import ALLOWED_FUNCTIONS, SYSTEM_INFORMATION_KEYWORDS

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


def _function_names(node: exp.Func) -> frozenset[str]:
    """节点对应的函数名集合，全部小写。取不到名字时返回空集合，按默认拒绝处理。"""
    if isinstance(node, exp.Anonymous):
        name = node.name
        if not isinstance(name, str) or not name:
            return frozenset()
        return frozenset({name.lower()})
    try:
        return frozenset(name.lower() for name in type(node).sql_names())
    except Exception:  # noqa: BLE001 名字不可知时按未知函数处理
        return frozenset()


def _check_cast_target(node: exp.Cast) -> PolicyDecision | None:
    """只允许转换到已知的内建数据类型。

    `'pg_class'::regclass`、`1::oid` 这类目标是对象标识类型，会绕过表引用直接
    解析系统对象；自定义类型的转换函数同样不可知。内建类型的目标节点携带
    `DataType.Type` 枚举，对象标识与伪类型只携带原始名字，据此区分。
    """
    target = node.args.get("to")
    if (
        not isinstance(target, exp.DataType)
        or not isinstance(target.this, exp.DataType.Type)
        or target.this in (exp.DataType.Type.USERDEFINED, exp.DataType.Type.UNKNOWN)
    ):
        rendered = target.sql(dialect=DIALECT) if target is not None else "未知"
        return _reject(
            "policy_forbidden_feature",
            f"不允许转换到类型 {rendered}",
        )
    return None


def _check_functions(root: exp.Expression) -> PolicyDecision | None:
    """函数调用必须落在安全允许集内；见 app/sql_functions.py 的收录标准。"""
    for node in root.walk():
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            # schema 限定调用可指向同名的自定义函数，名字判定无从覆盖
            return _reject(
                "policy_forbidden_function",
                f"不允许限定名函数调用 {node.sql(dialect=DIALECT)}",
            )
        if isinstance(node, exp.Cast):
            rejection = _check_cast_target(node)
            if rejection is not None:
                return rejection
        if isinstance(node, exp.Column) and not node.args.get("table"):
            identifier = node.this
            if (
                isinstance(identifier, exp.Identifier)
                and not identifier.quoted
                and identifier.name.lower() in SYSTEM_INFORMATION_KEYWORDS
            ):
                return _reject(
                    "policy_forbidden_function",
                    f"不允许调用系统信息表达式 {identifier.name.lower()}",
                )
        if isinstance(node, exp.Func):
            names = _function_names(node)
            if not names & ALLOWED_FUNCTIONS:
                return _reject(
                    "policy_forbidden_function",
                    f"不允许调用函数 {min(names) if names else type(node).__name__.lower()}",
                )
    return None


def _check_table(table: exp.Table, visible: frozenset[str]) -> PolicyDecision | None:
    """表引用要么命中当前作用域可见的 CTE 名，要么是契约表。"""
    catalog = _identifier_name(table.args.get("catalog"))
    schema = _identifier_name(table.args.get("db"))
    name = _identifier_name(table.args.get("this"))
    rejection = _reject(
        "policy_forbidden_object",
        f"不允许访问对象 {table.sql(dialect=DIALECT)}",
    )
    if catalog:
        return rejection
    if schema is None and name in visible:
        return None
    if schema not in (None, ANALYTICS_SCHEMA):
        return rejection
    if name not in CONTRACT_TABLES:
        return rejection
    return None


def _cte_name(cte: exp.CTE) -> str | None:
    alias = cte.args.get("alias")
    if not isinstance(alias, exp.TableAlias):
        return None
    return _identifier_name(alias.this)


def _check_object_scope(
    node: exp.Expression, visible: frozenset[str] = frozenset()
) -> PolicyDecision | None:
    """按真实词法作用域校验对象引用。

    `visible` 是在 `node` 处可见的 CTE 名。作用域规则同 SQL 标准：非递归 WITH 中，
    每个 CTE 体只能看见更早的兄弟（看不见自己），`WITH RECURSIVE` 下全部兄弟互相
    可见；内层 WITH 引入的名字不会外泄到定义它的作用域之外。名字不可见时该引用按
    物理表判定，因此与物理表同名的 CTE 无法把系统对象带进来。
    """
    with_node = next(
        (child for child in node.iter_expressions() if isinstance(child, exp.With)),
        None,
    )
    if with_node is not None:
        ctes = [cte for cte in with_node.expressions if isinstance(cte, exp.CTE)]
        names = [_cte_name(cte) for cte in ctes]
        recursive = bool(with_node.args.get("recursive"))
        siblings = frozenset(name for name in names if name)
        for index, cte in enumerate(ctes):
            earlier = frozenset(name for name in names[:index] if name)
            cte_visible = visible | (siblings if recursive else earlier)
            rejection = _check_object_scope(cte, cte_visible)
            if rejection is not None:
                return rejection
        visible = visible | siblings

    if isinstance(node, exp.Table):
        rejection = _check_table(node, visible)
        if rejection is not None:
            return rejection

    for child in node.iter_expressions():
        if child is with_node:
            continue
        rejection = _check_object_scope(child, visible)
        if rejection is not None:
            return rejection
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

    function_rejection = _check_functions(root)
    if function_rejection is not None:
        return function_rejection

    scope_rejection = _check_object_scope(root)
    if scope_rejection is not None:
        return scope_rejection

    return PolicyDecision(allowed=True, normalized_sql=root.sql(dialect=DIALECT))
