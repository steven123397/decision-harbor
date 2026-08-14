"""SQL governance policy: single read-only SELECT over authorized objects (AST-based)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import sqlglot
from sqlglot import exp

POLICY_PARSE_ERROR = "POLICY_PARSE_ERROR"
POLICY_EMPTY_STATEMENT = "POLICY_EMPTY_STATEMENT"
POLICY_MULTIPLE_STATEMENTS = "POLICY_MULTIPLE_STATEMENTS"
POLICY_NON_SELECT = "POLICY_NON_SELECT"
POLICY_FORBIDDEN_STATEMENT = "POLICY_FORBIDDEN_STATEMENT"
POLICY_DATA_MODIFYING_CTE = "POLICY_DATA_MODIFYING_CTE"
POLICY_SELECT_INTO = "POLICY_SELECT_INTO"
POLICY_UNAUTHORIZED_OBJECT = "POLICY_UNAUTHORIZED_OBJECT"

DEFAULT_ALLOWED_TABLES = frozenset(
    {"customers", "product_categories", "products", "orders", "order_items"}
)
DEFAULT_ALLOWED_SCHEMAS = frozenset({"analytics"})

_DATA_MODIFYING = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
_FORBIDDEN = (exp.Create, exp.Alter, exp.Drop, exp.TruncateTable, exp.Copy, exp.Command)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None
    tables: tuple[str, ...] = ()


def _deny(code: str, message: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, code=code, message=message)


def _allow(tables: Iterable[str]) -> PolicyDecision:
    return PolicyDecision(allowed=True, tables=tuple(sorted(set(tables))))


def _walk(root: exp.Expression):
    yield root
    yield from root.find_all(exp.Expression)


def _classify(node: exp.Expression) -> tuple[str, str] | None:
    if isinstance(node, _DATA_MODIFYING):
        if node.find_ancestor(exp.CTE) is not None:
            return (POLICY_DATA_MODIFYING_CTE, "Data-modifying CTEs are not allowed")
        return (POLICY_FORBIDDEN_STATEMENT, "Data-modifying statements are not allowed")
    if isinstance(node, _FORBIDDEN):
        return (POLICY_FORBIDDEN_STATEMENT, "This statement type is not allowed")
    if isinstance(node, exp.Select) and node.args.get("into") is not None:
        return (POLICY_SELECT_INTO, "SELECT INTO is not allowed")
    return None


def _is_select_like(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except))


def _table_allowed(
    table: exp.Table, allowed_tables: frozenset[str], allowed_schemas: frozenset[str]
) -> bool:
    name = (table.name or "").lower()
    if table.catalog:  # three-part name -> cross-database reference
        return False
    if table.db and table.db.lower() not in allowed_schemas:
        return False
    return name in allowed_tables


def check(
    sql: str | None,
    allowed_tables: frozenset[str] = DEFAULT_ALLOWED_TABLES,
    allowed_schemas: frozenset[str] = DEFAULT_ALLOWED_SCHEMAS,
) -> PolicyDecision:
    if sql is None or not sql.strip():
        return _deny(POLICY_EMPTY_STATEMENT, "SQL is empty")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception:
        return _deny(POLICY_PARSE_ERROR, "Could not parse SQL")

    if not statements:
        return _deny(POLICY_EMPTY_STATEMENT, "SQL is empty")
    if len(statements) != 1:
        return _deny(POLICY_MULTIPLE_STATEMENTS, "Multiple statements are not allowed")

    root = statements[0]

    for node in _walk(root):
        violation = _classify(node)
        if violation is not None:
            return _deny(violation[0], violation[1])

    if not _is_select_like(root):
        return _deny(POLICY_NON_SELECT, "Only a single read-only SELECT is allowed")

    cte_names = {cte.alias.lower() for cte in root.find_all(exp.CTE) if cte.alias}
    referenced: list[str] = []
    for table in root.find_all(exp.Table):
        if not table.name:
            continue
        name = table.name.lower()
        if name in cte_names:
            continue
        if not _table_allowed(table, allowed_tables, allowed_schemas):
            return _deny(POLICY_UNAUTHORIZED_OBJECT, f"Unauthorized object: {table.name}")
        referenced.append(name)

    return _allow(referenced)
