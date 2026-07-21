"""SQL 治理策略：基于 SQLGlot AST 与对象访问范围，fail-closed。

不依赖字符串黑名单。任何无法解析或无法判定的输入均拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

ANALYTICS_SCHEMA = "analytics"
ALLOWED_TABLES = frozenset(
    {
        (ANALYTICS_SCHEMA, "customers"),
        (ANALYTICS_SCHEMA, "product_categories"),
        (ANALYTICS_SCHEMA, "products"),
        (ANALYTICS_SCHEMA, "orders"),
        (ANALYTICS_SCHEMA, "order_items"),
    }
)
SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast"})

READ_QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
DML_TYPES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
DDL_TYPES = (exp.Create, exp.Alter, exp.Drop, exp.TruncateTable)


@dataclass
class PolicyResult:
    allowed: bool
    violations: list[str] = field(default_factory=list)
    object_scope: list[tuple[str, str]] = field(default_factory=list)


def _cte_names(stmt: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in stmt.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias is not None:
            names.add(alias.name.lower())
    return names


def analyze(sql: str) -> PolicyResult:
    if sql is None or not sql.strip():
        return PolicyResult(False, ["PARSE_ERROR"], [])

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception:
        return PolicyResult(False, ["PARSE_ERROR"], [])

    if not statements:
        return PolicyResult(False, ["PARSE_ERROR"], [])
    if len(statements) > 1:
        return PolicyResult(False, ["MULTI_STATEMENT"], [])

    stmt = statements[0]

    if not isinstance(stmt, READ_QUERY_TYPES):
        return PolicyResult(False, ["FORBIDDEN_STATEMENT"], [])

    violations: list[str] = []

    if stmt.find(exp.Into) is not None:
        violations.append("SELECT_INTO")

    if stmt.find(DML_TYPES) is not None:
        violations.append("DATA_MODIFYING_CTE")

    if stmt.find(DDL_TYPES) is not None or stmt.find(exp.Command) is not None:
        violations.append("FORBIDDEN_STATEMENT")

    cte_names = _cte_names(stmt)
    objects: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for tbl in stmt.find_all(exp.Table):
        schema = (tbl.db or ANALYTICS_SCHEMA).lower()
        name = tbl.name.lower()
        if name in cte_names:
            continue  # CTE 别名，非真实对象
        key = (schema, name)
        if key not in seen:
            seen.add(key)
            objects.append(key)
        if schema in SYSTEM_SCHEMAS or key not in ALLOWED_TABLES:
            if "FORBIDDEN_OBJECT" not in violations:
                violations.append("FORBIDDEN_OBJECT")

    return PolicyResult(allowed=not violations, violations=violations, object_scope=objects)
