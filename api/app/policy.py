from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = frozenset({
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
})

FORBIDDEN_PREFIXES = ("pg_", "information_schema")


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    allowed: bool
    code: str | None = None
    reason: str | None = None
    parsed_sql: str | None = None


def check_policy(sql: str, max_rows: int = 1000) -> PolicyVerdict:
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        return PolicyVerdict(allowed=False, code="PARSE_ERROR", reason=f"SQL 解析失败: {e}")

    real_statements = [s for s in statements if s is not None]

    if len(real_statements) == 0:
        return PolicyVerdict(allowed=False, code="PARSE_ERROR", reason="SQL 解析失败: 空语句")

    if len(real_statements) > 1:
        return PolicyVerdict(allowed=False, code="MULTI_STATEMENT", reason="不允许多条语句")

    statement = real_statements[0]

    if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        if isinstance(statement, exp.Command):
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_STATEMENT",
                reason=f"不允许的语句类型: {statement.this}",
            )
        return PolicyVerdict(
            allowed=False,
            code="FORBIDDEN_STATEMENT",
            reason=f"不允许的语句类型: {type(statement).__name__}",
        )

    for node in statement.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)):
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_CTE",
                reason="不允许数据修改型 CTE",
            )

    for select_node in statement.find_all(exp.Select):
        if select_node.args.get("into"):
            return PolicyVerdict(allowed=False, code="FORBIDDEN_INTO", reason="不允许 SELECT INTO")

    cte_names: set[str] = set()
    for cte in statement.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias:
            cte_names.add(alias.name.lower())

    for table in statement.find_all(exp.Table):
        table_name = table.name.lower()
        db = table.args.get("db")
        catalog = table.args.get("catalog")

        if table_name in cte_names and not db:
            continue

        if db and db.name.lower() == "information_schema":
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_OBJECT",
                reason=f"不允许访问系统目录: information_schema.{table_name}",
            )

        if catalog and catalog.name.lower().startswith(FORBIDDEN_PREFIXES):
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_OBJECT",
                reason=f"不允许访问系统目录: {catalog.name}",
            )

        if db and db.name.lower().startswith(FORBIDDEN_PREFIXES):
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_OBJECT",
                reason=f"不允许访问系统目录: {db.name}.{table_name}",
            )

        if table_name.startswith(FORBIDDEN_PREFIXES):
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_OBJECT",
                reason=f"不允许访问系统目录: {table_name}",
            )

        if table_name not in ALLOWED_TABLES:
            return PolicyVerdict(
                allowed=False,
                code="FORBIDDEN_OBJECT",
                reason=f"不允许访问对象: {table_name}",
            )

    limited = _enforce_limit(statement, max_rows)
    return PolicyVerdict(allowed=True, parsed_sql=limited.sql(dialect="postgres"))


def _enforce_limit(statement: exp.Select, max_rows: int) -> exp.Select:
    statement = statement.copy()
    existing_limit = statement.args.get("limit")
    if existing_limit is None:
        statement.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        limit_expr = existing_limit.expression
        if isinstance(limit_expr, exp.Literal) and limit_expr.is_int:
            if int(limit_expr.this) > max_rows:
                existing_limit.set("expression", exp.Literal.number(max_rows))
    return statement
