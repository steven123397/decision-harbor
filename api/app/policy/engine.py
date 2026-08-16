"""SQL 治理策略：基于 SQLGlot AST 与词法作用域的纯函数判定。

判定顺序与拒绝码语义见 docs/design/query-governance.md。
本包不访问数据库、不读配置文件；限额与授权表由调用方传入。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

QY_SQL_TOO_LONG = "QY_SQL_TOO_LONG"
QY_INVALID_SYNTAX = "QY_INVALID_SYNTAX"
QY_MULTIPLE_STATEMENTS = "QY_MULTIPLE_STATEMENTS"
QY_FORBIDDEN_STATEMENT = "QY_FORBIDDEN_STATEMENT"
QY_SELECT_INTO = "QY_SELECT_INTO"
QY_WRITE_CTE = "QY_WRITE_CTE"
QY_UNAUTHORIZED_OBJECT = "QY_UNAUTHORIZED_OBJECT"
QY_FORBIDDEN_FUNCTION = "QY_FORBIDDEN_FUNCTION"
QY_UNSUPPORTED_SQL = "QY_UNSUPPORTED_SQL"

# 数据库与 schema 名固定为 analytics；用户 SQL 只允许访问该范围。
ANALYTICS_DB = "analytics"

# 根节点白名单：查询表达式。
_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# 全树节点黑名单：写操作与命令类语句（嵌套出现也拒绝）。
_FORBIDDEN_NODES = (
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
    exp.Grant,
    exp.Set,
    # 行锁子句（FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE ...）
    exp.Lock,
)

# 函数白名单：常见分析聚合与标量函数。任何不在名单内的函数（含
# query_to_xml、current_setting、dblink 等系统信息与逃逸路径函数）一律拒绝。
_ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "CEIL",
        "COALESCE",
        "CONCAT",
        "COUNT",
        "DATE_TRUNC",
        "DENSE_RANK",
        "EXTRACT",
        "FLOOR",
        "LAG",
        "LEAD",
        "LENGTH",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "RANK",
        "ROUND",
        "ROW_NUMBER",
        "SUBSTRING",
        "SUM",
        "TRIM",
        "UPPER",
    }
)

# 结构性 Func 节点：CASE 表达式、CAST、EXISTS 与 CASE 分支本身不是
# 函数调用，跳过函数白名单（CAST 另有类型白名单）。
_STRUCTURAL_FUNCTIONS = (exp.Case, exp.Cast, exp.Exists, exp.If)

# CAST 目标类型白名单：仅常见标量类型。对象标识类型（regclass、oid 等，
# 解析为 ObjectIdentifier 或 USERDEFINED）与数组等复合类型一律拒绝。
_SAFE_CAST_TYPES = frozenset(
    t
    for t in (
        getattr(exp.DataType.Type, "BIGINT", None),
        getattr(exp.DataType.Type, "BOOLEAN", None),
        getattr(exp.DataType.Type, "CHAR", None),
        getattr(exp.DataType.Type, "DATE", None),
        getattr(exp.DataType.Type, "DECIMAL", None),
        getattr(exp.DataType.Type, "INT", None),
        getattr(exp.DataType.Type, "INTEGER", None),
        getattr(exp.DataType.Type, "NUMERIC", None),
        getattr(exp.DataType.Type, "SMALLINT", None),
        getattr(exp.DataType.Type, "TEXT", None),
        getattr(exp.DataType.Type, "TIMESTAMP", None),
        getattr(exp.DataType.Type, "TIMESTAMPTZ", None),
        getattr(exp.DataType.Type, "VARCHAR", None),
    )
    if t is not None
)
MAX_CHARACTER_CAST_LENGTH = 10_485_760
MAX_NUMERIC_CAST_PRECISION = 1_000

# 参数校验按类型分组；成员随 SQLGlot 版本差异用 getattr 容忍缺失。
_NUMERIC_TYPES = frozenset(
    t
    for t in (
        getattr(exp.DataType.Type, "DECIMAL", None),
        getattr(exp.DataType.Type, "NUMERIC", None),
    )
    if t is not None
)
_CHARACTER_TYPES = frozenset(
    t
    for t in (getattr(exp.DataType.Type, "CHAR", None), getattr(exp.DataType.Type, "VARCHAR", None))
    if t is not None
)
_TIMESTAMP_TYPES = frozenset(
    t
    for t in (
        getattr(exp.DataType.Type, "TIMESTAMP", None),
        getattr(exp.DataType.Type, "TIMESTAMPTZ", None),
    )
    if t is not None
)

# 支持节点白名单：全树遍历结束后，任何不在名单内的节点类型都拒绝。
# 名单按 SQLGlot 实际节点类型逐一确认（版本差异用 getattr 容忍）。
_SUPPORTED_NODES = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in (
            "Add", "Alias", "And", "Between", "Boolean", "Case", "Cast",
            "Column", "CTE", "DataType", "DataTypeParam", "Distinct", "Div",
            "EQ", "Except", "Exists", "Filter", "From", "Group", "GT", "GTE",
            "Having", "Identifier", "If", "ILike", "In", "Intersect", "Is",
            "Join", "Like", "Limit", "Literal", "LT", "LTE", "Mod", "Mul",
            "Neg", "NEQ", "Not", "Null", "Offset", "Or", "Order", "Ordered",
            "Paren", "Select", "Star", "Sub", "Subquery", "Table",
            "TableAlias", "Union", "Var", "Where", "Window", "WindowSpec",
            "With",
        )
    )
    if node is not None
) + (exp.Func,)


@dataclass(frozen=True)
class PolicyLimits:
    sql_max_length: int = 100_000


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None

    @classmethod
    def pass_(cls) -> "PolicyDecision":
        return cls(True)

    @classmethod
    def reject(cls, code: str, message: str) -> "PolicyDecision":
        return cls(False, code, message)


def evaluate(
    sql: str,
    *,
    limits: PolicyLimits | None = None,
    allowed_tables: frozenset[str] = frozenset(),
) -> PolicyDecision:
    """判定一条用户 SQL 是否允许执行；任何一步失败即返回对应拒绝码。"""
    limits = limits or PolicyLimits()

    if not isinstance(sql, str) or len(sql) > limits.sql_max_length:
        return PolicyDecision.reject(QY_SQL_TOO_LONG, "查询超过允许的最大长度")

    if not sql.strip():
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询不能为空")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询无法解析为合法的 SQL")

    statements = [s for s in statements if s is not None]
    if not statements:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "查询不能为空")
    if len(statements) != 1:
        return PolicyDecision.reject(QY_MULTIPLE_STATEMENTS, "仅允许单条查询语句")

    root = statements[0]
    if not isinstance(root, _QUERY_ROOTS):
        return PolicyDecision.reject(QY_FORBIDDEN_STATEMENT, "仅允许只读查询语句")

    if root.find(exp.Placeholder, exp.Parameter) is not None:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "不支持绑定参数占位符")

    if root.find(exp.Into) is not None:
        return PolicyDecision.reject(QY_SELECT_INTO, "不允许 SELECT INTO")

    # 数据修改型 CTE 优先于通用节点黑名单判定，保证稳定拒绝码。
    for cte in root.find_all(exp.CTE):
        if not isinstance(cte.this, _QUERY_ROOTS):
            return PolicyDecision.reject(QY_WRITE_CTE, "WITH 子句中不允许数据修改语句")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return PolicyDecision.reject(QY_FORBIDDEN_STATEMENT, "仅允许只读查询语句")

    # CTE 名不得与授权表同名：同名遮蔽会让语义依赖于解析顺序，直接拒绝。
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    if cte_names & {t.lower() for t in allowed_tables}:
        return _unauthorized()

    decision = _check_functions(root)
    if decision is not None:
        return decision

    decision = _check_casts(root)
    if decision is not None:
        return decision

    decision = _check_objects(root, allowed_tables)
    if decision is not None:
        return decision

    for node in root.walk():
        if not isinstance(node, _SUPPORTED_NODES):
            return PolicyDecision.reject(QY_UNSUPPORTED_SQL, "查询包含不支持的语法结构")

    return PolicyDecision.pass_()


def _check_functions(root: exp.Expression) -> PolicyDecision | None:
    for func in root.find_all(exp.Func):
        if isinstance(func, _STRUCTURAL_FUNCTIONS):
            continue
        name = (
            func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
        )
        # 方法调用形态（x.func(...)）连同点号访问一并拒绝。
        if isinstance(func.parent, exp.Dot) or name not in _ALLOWED_FUNCTIONS:
            return PolicyDecision.reject(QY_FORBIDDEN_FUNCTION, "查询使用了不允许的函数")
    return None


def _check_casts(root: exp.Expression) -> PolicyDecision | None:
    for cast in root.find_all(exp.Cast):
        target = cast.args.get("to")
        # 对象标识类型（regclass/oid 等）与无法归类目标都不是 DataType 白名单类型。
        if type(target) is not exp.DataType:
            return _unauthorized()
        assert isinstance(target, exp.DataType)
        if (
            target.this == exp.DataType.Type.USERDEFINED
            or target.args.get("kind") is not None
            or target.args.get("nested")
            or target.this not in _SAFE_CAST_TYPES
        ):
            return _unauthorized()
        decision = _check_cast_parameters(target)
        if decision is not None:
            return decision
    return None


def _check_cast_parameters(target: exp.DataType) -> PolicyDecision | None:
    def invalid() -> PolicyDecision:
        return PolicyDecision.reject(QY_INVALID_SYNTAX, "类型转换参数不被支持")

    values: list[int] = []
    for param in target.expressions or []:
        if not isinstance(param, exp.DataTypeParam):
            return invalid()
        literal = param.this
        if not isinstance(literal, exp.Literal) or literal.is_string:
            return invalid()
        try:
            values.append(int(literal.this))
        except (TypeError, ValueError):
            return invalid()

    if target.this in _NUMERIC_TYPES:
        if len(values) > 2 or any(v < 0 for v in values):
            return invalid()
        if values and not 1 <= values[0] <= MAX_NUMERIC_CAST_PRECISION:
            return invalid()
        if len(values) == 2 and values[1] > values[0]:
            return invalid()
        return None

    if target.this in _CHARACTER_TYPES:
        if len(values) > 1 or (values and not 1 <= values[0] <= MAX_CHARACTER_CAST_LENGTH):
            return invalid()
        return None

    if target.this in _TIMESTAMP_TYPES:
        if len(values) > 1 or (values and not 0 <= values[0] <= 6):
            return invalid()
        return None

    if values:
        return invalid()
    return None


def _check_objects(
    root: exp.Expression, allowed_tables: frozenset[str]
) -> PolicyDecision | None:
    """按词法作用域解析表引用：只有解析到物理 Table 的引用才做授权检查。

    CTE/子查询名在同一作用域内解析为对应查询定义，跳过；解析到 Table
    的裸名（含非递归 CTE 自引用回退到系统目录的情况）必须命中授权表。
    """
    allowed = {t.lower() for t in allowed_tables}
    for scope in traverse_scope(root):
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if source is not None and not isinstance(source, exp.Table):
                continue  # 该作用域内解析为 CTE / 子查询，不是物理表
            if isinstance(source, exp.Table) and source is not table:
                return _unauthorized()
            catalog = (table.catalog or "").lower()
            schema = (table.db or "").lower()
            name = table.name.lower()
            if catalog and catalog != ANALYTICS_DB:
                return _unauthorized()
            if schema:
                if schema != ANALYTICS_DB:
                    return _unauthorized()
            elif name not in allowed:
                return _unauthorized()
    return None


def _unauthorized() -> PolicyDecision:
    return PolicyDecision.reject(
        QY_UNAUTHORIZED_OBJECT, "仅允许访问 analytics 库的销售分析表"
    )
