from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope


ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "ceil",
        "coalesce",
        "concat",
        "count",
        "date_trunc",
        "dense_rank",
        "extract",
        "floor",
        "lag",
        "lead",
        "length",
        "lower",
        "max",
        "min",
        "nullif",
        "rank",
        "round",
        "row_number",
        "substring",
        "sum",
        "trim",
        "upper",
    }
)

ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
MAX_SQL_BYTES = 64 * 1024
STRUCTURAL_FUNCTION_NODES = (exp.Case, exp.Cast, exp.Exists, exp.If)
PROHIBITED_NODES = tuple(
    node_type
    for name in (
        "Alter",
        "Call",
        "Command",
        "Copy",
        "Create",
        "Delete",
        "Drop",
        "Execute",
        "Insert",
        "Into",
        "Lock",
        "Merge",
        "Transaction",
        "TruncateTable",
        "Update",
    )
    if (node_type := getattr(exp, name, None)) is not None
)
SUPPORTED_NODES = tuple(
    node_type
    for name in (
        "Add",
        "Alias",
        "And",
        "Between",
        "Boolean",
        "Case",
        "Cast",
        "Column",
        "CTE",
        "DataType",
        "DataTypeParam",
        "Distinct",
        "Div",
        "EQ",
        "Except",
        "Exists",
        "Filter",
        "From",
        "Group",
        "GT",
        "GTE",
        "Having",
        "Identifier",
        "If",
        "ILike",
        "In",
        "Intersect",
        "Is",
        "Join",
        "Like",
        "Limit",
        "Literal",
        "LT",
        "LTE",
        "Mod",
        "Mul",
        "Neg",
        "NEQ",
        "Not",
        "Null",
        "Offset",
        "Or",
        "Order",
        "Ordered",
        "Paren",
        "Select",
        "Star",
        "Sub",
        "Subquery",
        "Table",
        "TableAlias",
        "Union",
        "Where",
        "Window",
        "WindowSpec",
        "With",
    )
    if (node_type := getattr(exp, name, None)) is not None
) + (exp.Func,)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None
    summary: str | None
    referenced_objects: tuple[str, ...]


class SqlPolicy:
    def __init__(self, allowed_tables: set[str]) -> None:
        self._allowed_tables = frozenset(allowed_tables)

    def evaluate(self, raw_sql: str) -> PolicyDecision:
        if not raw_sql.strip():
            return self._reject("sql_empty", "SQL must not be empty.")
        if len(raw_sql.encode("utf-8")) > MAX_SQL_BYTES:
            return self._reject("sql_too_large", "SQL exceeds the 64 KiB limit.")

        try:
            expressions = sqlglot.parse(raw_sql, read="postgres")
        except ParseError:
            return self._reject("sql_parse_error", "SQL could not be parsed.")

        if len(expressions) != 1:
            return self._reject("multiple_statements", "Exactly one SQL statement is required.")
        if not expressions or not isinstance(expressions[0], ALLOWED_ROOTS):
            return self._reject("sql_statement_not_allowed", "Statement type is not allowed.")

        expression = expressions[0]
        if any(isinstance(node, PROHIBITED_NODES) for node in expression.walk()):
            return self._reject("sql_statement_not_allowed", "Statement contains a forbidden operation.")

        cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
        if cte_names & self._allowed_tables:
            return self._reject("sql_object_not_allowed", "CTE names cannot shadow analytics tables.")

        for function in expression.find_all(exp.Func):
            if isinstance(function, STRUCTURAL_FUNCTION_NODES):
                continue
            function_name = (
                function.name.lower()
                if isinstance(function, exp.Anonymous)
                else function.sql_name().lower()
            )
            if isinstance(function.parent, exp.Dot) or function_name not in ALLOWED_FUNCTIONS:
                return self._reject("sql_function_not_allowed", "Function is not allowed.")

        referenced_objects: set[str] = set()
        for scope in traverse_scope(expression):
            for table in scope.tables:
                source = scope.sources.get(table.alias_or_name)
                if not isinstance(source, exp.Table):
                    continue
                if source is not table:
                    return self._reject("unsupported_sql", "SQL contains an ambiguous table source.")
                table_name = table.name.lower()
                if table.catalog or table.db not in {"", "analytics"}:
                    return self._reject("sql_object_not_allowed", "Object is not allowed.")
                if table_name not in self._allowed_tables:
                    return self._reject("sql_object_not_allowed", "Object is not allowed.")
                referenced_objects.add(f"analytics.{table_name}")

        if any(not isinstance(node, SUPPORTED_NODES) for node in expression.walk()):
            return self._reject("unsupported_sql", "SQL contains an unsupported construct.")

        return PolicyDecision(
            allowed=True,
            code=None,
            summary=None,
            referenced_objects=tuple(sorted(referenced_objects)),
        )

    @staticmethod
    def _reject(code: str, summary: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            code=code,
            summary=summary,
            referenced_objects=(),
        )
