"""SQL 治理策略：基于 SQLGlot AST 与对象访问范围，无 IO 的纯函数。

判定步骤对应 docs/design/query-governance.md：
语法 → 单语句 → 根须为查询表达式 → 全树禁写操作 → 对象白名单。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

POLICY_INVALID_SYNTAX = "POLICY_INVALID_SYNTAX"
POLICY_MULTI_STATEMENT = "POLICY_MULTI_STATEMENT"
POLICY_NON_QUERY_STATEMENT = "POLICY_NON_QUERY_STATEMENT"
POLICY_WRITE_OPERATION = "POLICY_WRITE_OPERATION"
POLICY_UNAUTHORIZED_OBJECT = "POLICY_UNAUTHORIZED_OBJECT"

#: 契约五表（datasets/sales-analytics-v1/contract.json），不得改名或重解释。
DEFAULT_ALLOWED_TABLES = frozenset(
    {"customers", "product_categories", "products", "orders", "order_items"}
)
DEFAULT_SCHEMA = "analytics"

_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Alter,
    exp.Drop,
    exp.TruncateTable,
    exp.Copy,
    exp.Into,
)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    error_code: str | None = None
    message: str | None = None


def _reject(code: str, message: str) -> Decision:
    return Decision(allowed=False, error_code=code, message=message)


def _fold(name: str, quoted: bool) -> str:
    """PostgreSQL 标识符折叠：未加引号的标识符按小写解析。"""
    return name if quoted else name.lower()


def _quoted(node: object) -> bool:
    return isinstance(node, exp.Identifier) and bool(node.quoted)


def evaluate(
    sql: str,
    allowed_tables: frozenset[str] = DEFAULT_ALLOWED_TABLES,
    default_schema: str = DEFAULT_SCHEMA,
) -> Decision:
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.SqlglotError as exc:
        return _reject(POLICY_INVALID_SYNTAX, f"SQL 解析失败：{exc}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return _reject(POLICY_MULTI_STATEMENT, "一次提交只允许一条语句。")

    root = statements[0]
    if not isinstance(root, _QUERY_ROOTS):
        kind = root.key.upper()
        if isinstance(root, exp.Command):
            kind = str(root.args.get("this") or kind).upper()
        return _reject(
            POLICY_NON_QUERY_STATEMENT,
            f"只允许只读查询表达式（SELECT / WITH / UNION / INTERSECT / EXCEPT），收到：{kind}。",
        )
    if isinstance(root, exp.Select) and not root.expressions:
        return _reject(POLICY_INVALID_SYNTAX, "查询缺少投影列。")

    for node in root.walk():
        if isinstance(node, _WRITE_NODES):
            return _reject(
                POLICY_WRITE_OPERATION,
                f"查询中不允许写操作或 SELECT INTO：{node.key.upper()}。",
            )

    cte_names = {
        _fold(cte.alias, _quoted(cte.args.get("alias").this if cte.args.get("alias") else None))
        for cte in root.find_all(exp.CTE)
    }

    for table in root.find_all(exp.Table):
        name = table.name
        if not name:
            return _reject(
                POLICY_UNAUTHORIZED_OBJECT, "不允许访问非契约表对象（如表函数）。"
            )
        if _fold(name, _quoted(table.this)) in cte_names:
            continue  # CTE 遮蔽，不是真实表引用
        if table.catalog:
            return _reject(
                POLICY_UNAUTHORIZED_OBJECT,
                f"不允许跨数据库引用：{table.catalog}.{table.db or ''}{name}。",
            )
        db_node = table.args.get("db")
        if db_node is not None:
            schema = _fold(table.db, _quoted(db_node))
            if schema != default_schema:
                return _reject(
                    POLICY_UNAUTHORIZED_OBJECT,
                    f"不允许访问 schema {schema} 下的对象 {name}。",
                )
        if _fold(name, _quoted(table.this)) not in allowed_tables:
            return _reject(
                POLICY_UNAUTHORIZED_OBJECT, f"对象 {name} 不在允许的访问范围内。"
            )

    return Decision(allowed=True)
