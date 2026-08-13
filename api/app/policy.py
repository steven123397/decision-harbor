from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError


ALLOWED_TABLES = frozenset(
    {
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
    }
)
ALLOWED_SCHEMAS = frozenset({"", "analytics"})
ALLOWED_CATALOGS = frozenset({"", "analytics"})
ALLOWED_FUNCTION_SCHEMAS = frozenset({"", "pg_catalog"})

ALLOWED_FUNCTIONS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "row_number",
        "rank",
        "dense_rank",
        "lag",
        "lead",
        "first_value",
        "last_value",
        "ntile",
        "coalesce",
        "nullif",
        "abs",
        "round",
        "ceil",
        "ceiling",
        "floor",
        "mod",
        "greatest",
        "least",
        "sign",
        "trunc",
        "concat",
        "length",
        "lower",
        "upper",
        "trim",
        "ltrim",
        "rtrim",
        "substring",
        "replace",
        "left",
        "right",
        "date_trunc",
        "date_part",
        "extract",
        "age",
        "now",
        "current_date",
        "current_timestamp",
        "timezone",
        "to_char",
        "to_date",
        "to_timestamp",
        "cast",
        "timestamp_trunc",
        "time_to_str",
        "str_to_date",
        "str_to_time",
    }
)

_READ_QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_FORBIDDEN_TYPES = (
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
    exp.Set,
    exp.Grant,
    exp.Revoke,
    exp.Analyze,
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None


def evaluate_sql(sql: str) -> PolicyDecision:
    try:
        trees = sqlglot.parse(sql, read="postgres")
    except (ParseError, TokenError, ValueError):
        return _invalid("SQL could not be parsed as a single read-only query.")

    if len(trees) != 1 or trees[0] is None:
        return _invalid("SQL must contain exactly one read-only query.")

    root = trees[0]
    if not isinstance(root, _READ_QUERY_TYPES):
        return _denied("Only a single read-only SELECT or set operation is allowed.")

    try:
        _check_node(root, frozenset())
    except _PolicyDenied as exc:
        return _denied(str(exc))
    return PolicyDecision(allowed=True)


def _invalid(message: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, code="QUERY_INVALID", message=message)


def _denied(message: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, code="POLICY_DENIED", message=message)


class _PolicyDenied(Exception):
    pass


def _fold_ident(value: exp.Expression | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, exp.Identifier):
        raw = value.this or ""
        return raw if value.quoted else raw.lower()
    if isinstance(value, exp.Dot):
        parts = [_fold_ident(value.this), _fold_ident(value.expression)]
        return ".".join(part for part in parts if part)
    if isinstance(value, str):
        return value.lower()
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.lower()
    text = value.sql() if hasattr(value, "sql") else str(value)
    return text.lower()


def _cte_name(cte: exp.CTE) -> str:
    alias = cte.args.get("alias")
    if isinstance(alias, exp.TableAlias) and alias.this:
        return _fold_ident(alias.this)
    return _fold_ident(cte.alias)


def _check_node(node: exp.Expression, cte_scope: frozenset[str]) -> None:
    scope = cte_scope
    if isinstance(node, _READ_QUERY_TYPES):
        _reject_query_modifiers(node)
        with_clause = node.args.get("with_")
        if with_clause:
            scope = _check_with(with_clause, scope)

    if isinstance(node, _FORBIDDEN_TYPES):
        raise _PolicyDenied(f"Statement type {node.key} is not allowed.")

    if isinstance(node, exp.Table):
        _check_table(node, scope)

    if isinstance(node, exp.Func):
        _check_function(node)

    for child in node.iter_expressions():
        if node.args.get("with_") is child:
            continue
        _check_node(child, scope)


def _reject_query_modifiers(node: exp.Expression) -> None:
    if node.args.get("into") is not None:
        raise _PolicyDenied("SELECT INTO is not allowed.")
    locks = node.args.get("locks")
    if locks:
        raise _PolicyDenied("Row locks are not allowed.")


def _check_with(with_clause: exp.With, outer_scope: frozenset[str]) -> frozenset[str]:
    scope = set(outer_scope)
    ctes = list(with_clause.expressions)
    recursive = bool(with_clause.args.get("recursive"))
    if recursive:
        scope.update(_cte_name(cte) for cte in ctes)
    for cte in ctes:
        _check_node(cte.this, frozenset(scope))
        if not recursive:
            scope.add(_cte_name(cte))
    return frozenset(scope)


def _check_table(table: exp.Table, cte_scope: frozenset[str]) -> None:
    this = table.this
    if not isinstance(this, exp.Identifier):
        raise _PolicyDenied("Only contracted analytics tables may be referenced.")

    name = _fold_ident(this)
    schema = _fold_ident(table.args.get("db"))
    catalog = _fold_ident(table.args.get("catalog"))

    if name in cte_scope and schema == "" and catalog == "":
        return
    if name not in ALLOWED_TABLES or schema not in ALLOWED_SCHEMAS or catalog not in ALLOWED_CATALOGS:
        raise _PolicyDenied("Query references an object outside the analytics access scope.")


def _check_function(func: exp.Func) -> None:
    if isinstance(func, exp.Cast):
        return

    name = _function_name(func)
    if name not in ALLOWED_FUNCTIONS:
        raise _PolicyDenied(f"Function {name} is not allowed.")

    schema = _function_schema(func)
    if schema not in ALLOWED_FUNCTION_SCHEMAS:
        raise _PolicyDenied("Only unqualified or pg_catalog functions are allowed.")


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous) and isinstance(func.this, str):
        return func.this.lower()
    sql_name = func.sql_name() if hasattr(func, "sql_name") else ""
    if sql_name and sql_name.lower() != "anonymous":
        return sql_name.lower()
    if isinstance(func.this, str) and func.this:
        return func.this.lower()
    return (func.key or "").lower()


def _function_schema(func: exp.Func) -> str:
    parent = func.parent
    if isinstance(parent, exp.Dot) and parent.expression is func:
        return _fold_ident(parent.this)
    return ""
