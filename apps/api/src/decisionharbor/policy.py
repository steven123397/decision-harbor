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
SAFE_CAST_TYPES = frozenset(
    {
        exp.DataType.Type.BIGINT,
        exp.DataType.Type.BOOLEAN,
        exp.DataType.Type.CHAR,
        exp.DataType.Type.DATE,
        exp.DataType.Type.DECIMAL,
        exp.DataType.Type.INT,
        exp.DataType.Type.SMALLINT,
        exp.DataType.Type.TEXT,
        exp.DataType.Type.TIMESTAMP,
        exp.DataType.Type.TIMESTAMPTZ,
        exp.DataType.Type.VARCHAR,
    }
)
MAX_CHARACTER_CAST_LENGTH = 10_485_760
MAX_NUMERIC_PRECISION = 1_000
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

        cast_rejection = self._validate_casts(expression)
        if cast_rejection:
            return cast_rejection

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

    def _validate_casts(self, expression: exp.Expression) -> PolicyDecision | None:
        for cast in expression.find_all(exp.Cast):
            target = cast.args.get("to")
            if type(target) is not exp.DataType:
                return self._reject("sql_object_not_allowed", "Cast type is not allowed.")
            if target.this == exp.DataType.Type.USERDEFINED or target.args.get("kind") is not None:
                return self._reject("sql_object_not_allowed", "Cast type is not allowed.")
            if target.this not in SAFE_CAST_TYPES or target.args.get("nested"):
                return self._reject("unsupported_sql", "Cast type is not supported.")

            parameter_rejection = self._validate_cast_parameters(target)
            if parameter_rejection:
                return parameter_rejection
        return None

    def _validate_cast_parameters(self, target: exp.DataType) -> PolicyDecision | None:
        parameters = target.expressions or []
        values: list[int] = []
        for parameter in parameters:
            if not isinstance(parameter, exp.DataTypeParam):
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            literal = parameter.this
            if not isinstance(literal, exp.Literal) or literal.is_string:
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            try:
                values.append(int(literal.this))
            except (TypeError, ValueError):
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")

        if target.this == exp.DataType.Type.DECIMAL:
            if len(values) > 2 or any(value < 0 for value in values):
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            if values and not 1 <= values[0] <= MAX_NUMERIC_PRECISION:
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            if len(values) == 2 and values[1] > values[0]:
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            return None

        if target.this in {exp.DataType.Type.CHAR, exp.DataType.Type.VARCHAR}:
            if len(values) > 1 or (values and not 1 <= values[0] <= MAX_CHARACTER_CAST_LENGTH):
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            return None

        if target.this in {exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ}:
            if len(values) > 1 or (values and not 0 <= values[0] <= 6):
                return self._reject("unsupported_sql", "Cast type parameters are not supported.")
            return None

        if values:
            return self._reject("unsupported_sql", "Cast type parameters are not supported.")
        return None

    @staticmethod
    def _reject(code: str, summary: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            code=code,
            summary=summary,
            referenced_objects=(),
        )
