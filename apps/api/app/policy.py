"""SQL governance via SQLGlot AST and object allow-list."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

import sqlglot
from sqlglot import exp


ALLOWED_TABLES: Final[set[str]] = {
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
}

ALLOWED_SCHEMAS: Final[set[str]] = {"public"}

MAX_SQL_CHARS: Final[int] = 100 * 1024

def _expr_types(*names: str) -> tuple[type[exp.Expression], ...]:
    """Resolve optional SQLGlot expression classes across versions."""
    found: list[type[exp.Expression]] = []
    for name in names:
        cls = getattr(exp, name, None)
        if isinstance(cls, type) and issubclass(cls, exp.Expression):
            found.append(cls)
    return tuple(found)


# Statement / node types that are never allowed in user SQL.
_FORBIDDEN_NODE_TYPES: Final[tuple[type[exp.Expression], ...]] = _expr_types(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Grant",
    "Revoke",
    "Set",
    "Use",
    "Transaction",
    "Commit",
    "Rollback",
    "Copy",
)


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: PolicyDecision
    error_code: str | None = None
    error_message: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW


class SqlPolicy:
    """Check user SQL against AST shape and object access rules.

    Does not rewrite SQL. Does not execute SQL.
    """

    def __init__(
        self,
        *,
        allowed_tables: set[str] | None = None,
        max_sql_chars: int = MAX_SQL_CHARS,
    ) -> None:
        self._allowed_tables = allowed_tables or set(ALLOWED_TABLES)
        self._max_sql_chars = max_sql_chars

    def check(self, sql: str) -> PolicyResult:
        if not isinstance(sql, str):
            return self._reject("POLICY_PARSE_ERROR", "SQL must be a string")

        if len(sql) > self._max_sql_chars:
            return self._reject(
                "POLICY_SQL_TOO_LARGE",
                f"SQL exceeds maximum length of {self._max_sql_chars} characters",
            )

        stripped = sql.strip()
        if not stripped:
            return self._reject("POLICY_PARSE_ERROR", "SQL is empty")

        try:
            statements = sqlglot.parse(stripped, read="postgres")
        except sqlglot.errors.ParseError as exc:
            return self._reject("POLICY_PARSE_ERROR", f"Failed to parse SQL: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface as parse/policy failure
            return self._reject("POLICY_PARSE_ERROR", f"Failed to parse SQL: {exc}")

        # sqlglot may return [None] for empty-ish inputs
        statements = [s for s in statements if s is not None]
        if len(statements) == 0:
            return self._reject("POLICY_PARSE_ERROR", "SQL produced no statements")
        if len(statements) > 1:
            return self._reject(
                "POLICY_MULTI_STATEMENT",
                "Only a single SQL statement is allowed",
            )

        root = statements[0]

        if self._contains_forbidden_nodes(root):
            return self._reject(
                "POLICY_FORBIDDEN_STATEMENT",
                "Statement type or clause is not allowed",
            )

        if not self._is_read_query(root):
            return self._reject(
                "POLICY_FORBIDDEN_STATEMENT",
                "Only read-only SELECT queries are allowed",
            )

        object_error = self._check_objects(root)
        if object_error is not None:
            return object_error

        return PolicyResult(decision=PolicyDecision.ALLOW)

    def _reject(self, code: str, message: str) -> PolicyResult:
        return PolicyResult(
            decision=PolicyDecision.REJECT,
            error_code=code,
            error_message=message,
        )

    def _contains_forbidden_nodes(self, root: exp.Expression) -> bool:
        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODE_TYPES):
                return True
            # SELECT INTO (PostgreSQL)
            if isinstance(node, exp.Select) and node.args.get("into") is not None:
                return True
            # Data-modifying CTEs: WITH ... INSERT/UPDATE/DELETE
            if isinstance(node, exp.CTE):
                this = node.this
                if this is not None and isinstance(this, _FORBIDDEN_NODE_TYPES):
                    return True
                if this is not None and not isinstance(
                    this, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
                ):
                    # Any non-read CTE body is forbidden
                    if isinstance(this, exp.Expression) and not isinstance(
                        this, exp.Query
                    ):
                        # Allow nested query shapes; reject DML already covered
                        pass
        return False

    def _is_read_query(self, root: exp.Expression) -> bool:
        return isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With))

    def _check_objects(self, root: exp.Expression) -> PolicyResult | None:
        cte_names: set[str] = set()
        for cte in root.find_all(exp.CTE):
            alias = cte.alias
            if alias:
                cte_names.add(alias.lower())

        for table in root.find_all(exp.Table):
            name = (table.name or "").lower()
            if not name:
                continue

            # CTE reference
            if name in cte_names:
                continue

            schema = table.db or table.catalog
            # sqlglot: catalog.db.table — for "public.customers", db=public
            # for "pg_catalog.pg_tables", db=pg_catalog
            schema_name = None
            if table.db:
                schema_name = table.db.lower()
            if table.catalog:
                # catalog.schema.table form — reject non-empty catalog as cross-db
                return self._reject(
                    "POLICY_FORBIDDEN_OBJECT",
                    f"Catalog-qualified object is not allowed: {table.sql()}",
                )

            if schema_name and schema_name not in ALLOWED_SCHEMAS:
                return self._reject(
                    "POLICY_FORBIDDEN_OBJECT",
                    f"Schema is not allowed: {schema_name}",
                )

            if name not in self._allowed_tables:
                return self._reject(
                    "POLICY_FORBIDDEN_OBJECT",
                    f"Table is not allowed: {name}",
                )

        return None
