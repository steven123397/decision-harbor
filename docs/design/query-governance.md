# SQL 治理与资源限制

## 范围

API 内查询策略的判定流程、拒绝语义与执行资源限制。数据库身份边界见 [data-and-seeding.md](data-and-seeding.md)，HTTP 错误语义见 [api.md](api.md)。

## 目标与非目标

目标：基于 SQLGlot AST 与对象访问范围，拒绝一切非只读语句与非授权对象访问；为执行配置语句超时与结果行数上限。

非目标：查询改写与优化、限流与用户配额、性能分析。

## 判定流程

策略模块（`app/policy/`）是纯函数，不访问数据库：`evaluate(sql) -> PolicyDecision`（通过，或拒绝码 + 可读说明）。判定按以下顺序执行，任一步失败即返回对应拒绝码：

1. **静态检查**：SQL 长度不超过上限（默认 100,000 字符，`QUERY_SQL_MAX_LENGTH` 可配），去除注释后非空。超限拒绝 `QY_SQL_TOO_LONG`。
2. **解析**：`sqlglot.parse(sql, read="postgres")` 必须恰好产生一条语句；解析失败拒绝 `QY_INVALID_SYNTAX`，多条语句拒绝 `QY_MULTIPLE_STATEMENTS`。含参数占位符同样拒绝 `QY_INVALID_SYNTAX`（首轮不支持绑定参数，SQL 按原文执行）。
3. **根节点类型白名单**：根节点必须是查询表达式——`SELECT`，或 `UNION`、`INTERSECT`、`EXCEPT` 集合操作表达式；`WITH ... SELECT` 解析后以查询根节点呈现。其余一律拒绝 `QY_FORBIDDEN_STATEMENT`。
4. **全树节点扫描**：遍历 AST，命中写操作或命令类节点即拒绝 `QY_FORBIDDEN_STATEMENT`。判定以节点类型清单实现，覆盖 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO` 与无法归类的 `Command` 节点；清单以 SQLGlot 实际类型为准在实现时补全，并由单元测试锁定（见 [testing.md](testing.md)）。`SELECT INTO`（`Into` 节点）拒绝 `QY_SELECT_INTO`。
5. **数据修改型 CTE**：`WITH` 子句中出现任何非查询语句（如 `INSERT ... RETURNING`）拒绝 `QY_WRITE_CTE`。
6. **对象访问范围**：收集全部表引用（含子查询、CTE 引用与函数内查询），逐一解析限定名：
   - 显式指定数据库或 schema 且不等于 `analytics` 的，拒绝 `QY_UNAUTHORIZED_OBJECT`——该规则同时覆盖系统目录（`pg_catalog`、`information_schema`）与其他 schema；
   - 未限定的名字必须在授权表白名单（契约五张表，见 `datasets/sales-analytics-v1/contract.json`）或本次查询定义的 CTE 名单中，否则同样拒绝 `QY_UNAUTHORIZED_OBJECT`。
7. **函数黑名单**：命中 `pg_read_file`、`pg_read_binary_file`、`lo_import`、`lo_export`、`dblink`、`dblink_exec`、`pg_sleep` 等已知危险函数即拒绝 `QY_FORBIDDEN_FUNCTION`。

策略通过后不改写 SQL，按原文交给执行器；原始 SQL 同时落审计记录。

## 防线分层

策略检查是第一道边界，不单独承担安全责任：

1. AST 与对象白名单（应用层，主判定）；
2. `analytics_readonly` 数据库身份——即使策略漏判，写入、DDL 与未授权对象访问在数据库层失败（见 [data-and-seeding.md](data-and-seeding.md)）；
3. 函数黑名单与角色级默认超时——针对已知逃逸路径的纵深防御。

## 资源限制

- **语句超时**：执行连接设置 `statement_timeout`（默认 10,000 ms，`QUERY_STATEMENT_TIMEOUT_MS` 可配），并在角色级设置同值作为兜底。超时错误映射为失败 `QY_TIMEOUT`。
- **结果行数上限**：应用侧流式取数，达到上限（默认 1,000 行，`QUERY_MAX_ROWS` 可配）即停止取数并标记截断，语义与理由见 [api.md](api.md)。
- **连接池**：只读执行使用独立小连接池（默认上限 5），与平台写入池隔离，避免用户查询耗尽审计写入能力。
