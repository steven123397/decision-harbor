from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError


MAX_SQL_BYTES = 64 * 1024
MAX_RESULT_ROWS = 10_000
ALLOWED_SCHEMA = "analytics"
ALLOWED_TABLES = frozenset(
    {
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
    }
)
ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "CEIL",
        "COALESCE",
        "COUNT",
        "DATE_PART",
        "DENSE_RANK",
        "EXTRACT",
        "FLOOR",
        "GREATEST",
        "LAG",
        "LEAD",
        "LENGTH",
        "LEAST",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "RANK",
        "ROUND",
        "ROW_NUMBER",
        "STRING_AGG",
        "SUBSTRING",
        "SUM",
        "TIMESTAMP_TRUNC",
        "TRIM",
        "UPPER",
    }
)
ALLOWED_CAST_TYPES = frozenset(
    {
        "BIGINT",
        "CHAR",
        "DATE",
        "DECIMAL",
        "INT",
        "INTEGER",
        "NUMERIC",
        "TEXT",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "VARCHAR",
    }
)


def _classes(*names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(cls for name in names if (cls := getattr(exp, name, None)) is not None)


_WRITE_NODES = _classes(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Alter",
    "Drop",
    "TruncateTable",
    "Copy",
    "Command",
    "Set",
    "Transaction",
    "Explain",
    "Show",
    "Use",
)
_DML_NODES = _classes("Insert", "Update", "Delete", "Merge")
_READ_ROOTS = _classes("Select", "Union", "Intersect", "Except")

@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None
    message: str
    referenced_objects: tuple[str, ...] = ()


def evaluate_sql(
    sql: str,
    *,
    max_sql_bytes: int = MAX_SQL_BYTES,
    max_result_rows: int = MAX_RESULT_ROWS,
) -> PolicyDecision:
    if not isinstance(sql, str) or not sql.strip():
        return PolicyDecision(False, "sql_empty", "SQL must not be empty")
    if len(sql.encode("utf-8")) > max_sql_bytes:
        return PolicyDecision(False, "sql_too_large", "SQL exceeds the maximum input size")

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return PolicyDecision(False, "sql_parse_error", "SQL could not be parsed")

    if len(statements) != 1:
        return PolicyDecision(False, "multiple_statements", "Only one SQL statement is allowed")

    root = statements[0]
    ctes = tuple(root.find_all(exp.CTE))
    cte_names = {cte.alias_or_name.lower() for cte in ctes if cte.alias_or_name}
    if any(isinstance(node, _DML_NODES) for cte in ctes for node in cte.walk()):
        return PolicyDecision(False, "write_cte", "Data-modifying CTEs are not allowed")
    if not isinstance(root, _READ_ROOTS):
        return PolicyDecision(False, "non_read_query", "Only read-only query expressions are allowed")
    if any(isinstance(node, _WRITE_NODES) for node in root.walk()):
        return PolicyDecision(False, "non_read_query", "Only read-only query expressions are allowed")
    if any(isinstance(node, exp.Into) for node in root.walk()):
        return PolicyDecision(False, "select_into", "SELECT INTO is not allowed")
    if any(isinstance(node, cls) for cls in _classes("Lock") for node in root.walk()):
        return PolicyDecision(False, "non_read_query", "Locking reads are not allowed")

    object_result = _check_objects(root, cte_names)
    if object_result is not None:
        return object_result

    function_result = _check_functions(root)
    if function_result is not None:
        return function_result

    limit_result = _check_literal_limit(root, max_result_rows)
    if limit_result is not None:
        return limit_result

    referenced_objects = tuple(sorted(_referenced_objects(root, cte_names)))
    return PolicyDecision(True, None, "SQL policy accepted", referenced_objects)


def _check_objects(root: exp.Expression, cte_names: set[str]) -> PolicyDecision | None:
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db or None
        catalog = table.catalog or None
        if not schema and not catalog and name in cte_names:
            continue
        if catalog or (schema is not None and schema.lower() != ALLOWED_SCHEMA) or name not in ALLOWED_TABLES:
            return PolicyDecision(False, "object_not_allowed", "Query references an unauthorized object")
    return None


def _referenced_objects(root: exp.Expression, cte_names: set[str]) -> set[str]:
    objects: set[str] = set()
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        if not table.db and not table.catalog and name in cte_names:
            continue
        objects.add(f"{ALLOWED_SCHEMA}.{name}")
    return objects


def _check_functions(root: exp.Expression) -> PolicyDecision | None:
    for function in root.find_all(exp.Func):
        if isinstance(function, exp.Cast):
            target = function.args.get("to")
            if not isinstance(target, exp.DataType):
                return PolicyDecision(False, "function_not_allowed", "Only safe scalar casts are allowed")
            type_name = str(target.this).upper().split(".")[-1]
            if type_name not in ALLOWED_CAST_TYPES:
                return PolicyDecision(False, "function_not_allowed", "Only safe scalar casts are allowed")
            continue
        if function.sql_name().upper() not in ALLOWED_FUNCTIONS:
            return PolicyDecision(False, "function_not_allowed", "Function is not allowed")
    return None


def _check_literal_limit(root: exp.Expression, max_result_rows: int) -> PolicyDecision | None:
    for limit in root.find_all(exp.Limit):
        value = limit.args.get("expression")
        if isinstance(value, exp.Literal) and not value.is_string:
            try:
                if int(value.this) > max_result_rows:
                    return PolicyDecision(False, "result_limit_exceeded", "Requested row limit exceeds the maximum")
            except ValueError:
                return PolicyDecision(False, "result_limit_exceeded", "Requested row limit is invalid")
    return None
