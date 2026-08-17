# 表引用按 SQLGlot 词法作用域解析

表引用校验使用 `traverse_scope` 逐作用域解析，而不是全局收集 CTE 名再相减。引用在所在作用域内解析为 CTE / 子查询定义时跳过；解析回物理表的引用逐一校验限定名与白名单——包括非递归 CTE 体内自引用：PostgreSQL 会把这类引用回退到 `pg_catalog` 真表，是已知的绕过路径。CTE 名与授权表同名直接拒绝（`QY_UNAUTHORIZED_OBJECT`），消除「裸名解析到谁」的歧义。

动机是 2026-08-16 评审轮封堵的 CTE 遮蔽 / `::regclass` / `query_to_xml` / `current_user` 等绕过向量；这些向量在单元与集成两层有回归锁定。
