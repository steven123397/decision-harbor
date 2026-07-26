"""SQL 治理策略：基于 SQLGlot AST、词法作用域与对象访问范围，无 IO 的纯函数。

判定步骤对应 docs/design/query-governance.md：
语法 → 单语句 → 根须为查询表达式 → 全树禁写操作 → schema 限定函数（词法）
→ 函数允许集 → 对象白名单（词法作用域解析真实表引用）。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope
from sqlglot.tokens import Tokenizer, TokenType

POLICY_INVALID_SYNTAX = "POLICY_INVALID_SYNTAX"
POLICY_MULTI_STATEMENT = "POLICY_MULTI_STATEMENT"
POLICY_NON_QUERY_STATEMENT = "POLICY_NON_QUERY_STATEMENT"
POLICY_WRITE_OPERATION = "POLICY_WRITE_OPERATION"
POLICY_UNAUTHORIZED_OBJECT = "POLICY_UNAUTHORIZED_OBJECT"
POLICY_UNSAFE_FUNCTION = "POLICY_UNSAFE_FUNCTION"

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

#: 函数允许集：只读、不解释 SQL 文本、不访问系统对象的函数。
#: 前四个是 sqlglot 把逻辑/谓词构造也建模为 Func 的语法节点，并非可调用函数。
ALLOWED_FUNCTIONS = frozenset(
    {
        # 语法构造（AND/OR/NOT/EXISTS/CASE/IF 在 sqlglot 中是 Func 子类）
        "AND", "OR", "NOT", "EXISTS", "CASE", "IF",
        # 类型转换与取值
        "CAST", "EXTRACT",
        # 聚合（GROUP_CONCAT 是 string_agg 归一化后的名字）
        "COUNT", "SUM", "AVG", "MIN", "MAX",
        "EVERY", "BOOL_AND", "BOOL_OR", "STRING_AGG", "GROUP_CONCAT", "ARRAY_AGG",
        # 窗口
        "ROW_NUMBER", "RANK", "DENSE_RANK", "NTILE",
        "LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE",
        # 数学
        "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "TRUNC",
        "MOD", "POWER", "SQRT", "EXP", "LN", "LOG", "SIGN",
        # 字符串
        "LOWER", "UPPER", "LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH",
        "SUBSTRING", "SUBSTR", "POSITION", "TRIM", "BTRIM", "LTRIM", "RTRIM",
        "CONCAT", "CONCAT_WS", "LEFT", "RIGHT", "REPLACE", "REVERSE",
        "REPEAT", "LPAD", "RPAD", "SPLIT_PART", "STARTS_WITH",
        "INITCAP", "ASCII", "CHR", "TRANSLATE",
        # 条件
        "COALESCE", "NULLIF", "GREATEST", "LEAST",
        # 日期时间
        "NOW", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP",
        "LOCALTIME", "LOCALTIMESTAMP",
        # TIMESTAMP_TRUNC 是 date_trunc 归一化后的名字
        "DATE_TRUNC", "TIMESTAMP_TRUNC", "DATE_PART", "AGE",
        "TO_CHAR", "TO_DATE", "TO_TIMESTAMP",
    }
)

_IDENT_TOKENS = (TokenType.VAR, TokenType.IDENTIFIER)


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


def _has_schema_qualified_call(sql: str) -> str | None:
    """词法检测 `schema.func(` 形式的调用。

    sqlglot 解析时会丢弃函数调用的 schema 限定（pg_catalog.pg_sleep(1) 与
    pg_sleep(1) 得到同一 AST），因此用 token 序列补充判定。这是词法分析
    而非字符串匹配：字符串字面量、注释与列引用（c.id）不会误判。
    """
    tokens = Tokenizer().tokenize(sql)
    for first, second, third, fourth in zip(tokens, tokens[1:], tokens[2:], tokens[3:]):
        if (
            first.token_type in _IDENT_TOKENS
            and second.token_type is TokenType.DOT
            and third.token_type in _IDENT_TOKENS
            and fourth.token_type is TokenType.L_PAREN
        ):
            return f"{first.text}.{third.text}"
    return None


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return (node.name or "").upper()
    return node.sql_name().upper()


def _check_functions(root: exp.Expression) -> Decision | None:
    for node in root.walk():
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name not in ALLOWED_FUNCTIONS:
                return _reject(
                    POLICY_UNSAFE_FUNCTION,
                    f"函数 {name or '未知'} 不在允许的只读函数集内。",
                )
    return None


def _check_objects(
    root: exp.Expression,
    allowed_tables: frozenset[str],
    default_schema: str,
) -> Decision | None:
    # 表函数与非普通表引用（如 generate_series）
    for table in root.find_all(exp.Table):
        if not table.name:
            return _reject(
                POLICY_UNAUTHORIZED_OBJECT, "不允许访问非契约表对象（如表函数）。"
            )

    # 词法作用域：source 为 exp.Table 的才是真实表引用；
    # 为 Scope 的是 CTE/派生表（只读表达式，不触碰数据库对象）。
    try:
        scope = build_scope(root)
    except Exception:
        scope = None
    if scope is None:
        return _reject(
            POLICY_UNAUTHORIZED_OBJECT, "无法完成作用域分析，按未授权对象保守拒绝。"
        )

    seen: set[int] = set()
    for sc in scope.traverse():
        for source in sc.sources.values():
            if not isinstance(source, exp.Table) or id(source) in seen:
                continue
            seen.add(id(source))
            table = source
            name = table.name
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
    return None


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

    qualified_call = _has_schema_qualified_call(sql)
    if qualified_call is not None:
        return _reject(
            POLICY_UNSAFE_FUNCTION,
            f"不允许 schema 限定的函数调用：{qualified_call}。",
        )

    return _check_objects(root, allowed_tables, default_schema) or _check_functions(root) or Decision(
        allowed=True
    )
