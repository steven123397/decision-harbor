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
6. **CTE 名遮蔽**：CTE 名与授权表同名时拒绝 `QY_UNAUTHORIZED_OBJECT`。同名遮蔽会让「裸名到底解析到 CTE 还是物理表」依赖解析顺序，直接拒绝以消除歧义。
7. **函数白名单**：函数调用只允许常见分析聚合与标量函数（`count`、`sum`、`avg`、`min`、`max`、`round`、`coalesce`、`lower`/`upper`、窗口序号函数、`date_trunc`/`extract` 等完整清单见实现并由单元测试锁定）。白名单之外一律拒绝 `QY_FORBIDDEN_FUNCTION`——`query_to_xml`、`current_user`、`current_setting`、`version`、`dblink` 等系统信息与逃逸路径函数天然不在名单内；方法调用形态（`x.func(...)`）同样拒绝。
8. **CAST 类型白名单**：类型转换目标只允许常见标量类型（整数、数值、文本、日期时间、布尔）。对象标识类型（`regclass`、`oid` 等）、数组与复合类型、未知类型拒绝 `QY_UNAUTHORIZED_OBJECT`；类型参数越界（超长 varchar、超精度 numeric 等）拒绝 `QY_INVALID_SYNTAX`。
9. **对象访问范围（按词法作用域）**：使用 SQLGlot 的作用域遍历（`traverse_scope`）逐作用域解析表引用，而不是全局收集 CTE 名：
   - 引用在所在作用域内解析为 CTE / 子查询定义时，不是物理表，跳过；
   - 解析为物理表的引用逐一校验限定名：显式指定数据库或 schema 且不等于 `analytics` 的拒绝 `QY_UNAUTHORIZED_OBJECT`（覆盖 `pg_catalog`、`information_schema` 等）；未限定名必须在授权表白名单（契约五张表）内；
   - 非递归 CTE 体内自引用（如 `WITH pg_class AS (SELECT relname FROM pg_class) ...`）在作用域内解析回物理表，同样必须命中白名单——PostgreSQL 中这类引用会回退到 `pg_catalog` 真表，是已知的绕过路径。
10. **支持节点白名单**：全树遍历结束后，任何不在支持节点清单内的 AST 节点类型拒绝 `QY_UNSUPPORTED_SQL`。清单显式枚举首轮支持的查询结构（连接、子查询、CTE、集合操作、窗口、CASE 等），节点类型不在清单内即视为不支持，防止新语法逃逸。

策略通过后不改写 SQL，按原文交给执行器；原始 SQL 同时落审计记录。

## 防线分层

策略检查是第一道边界，不单独承担安全责任：

1. AST、函数与类型白名单（应用层，主判定）；
2. `analytics_readonly` 数据库身份——即使策略漏判，写入、DDL 与未授权对象访问在数据库层失败（见 [data-and-seeding.md](data-and-seeding.md)）；
3. 角色级默认超时——针对残余风险的纵深防御。

## 资源限制

- **语句超时**：执行连接设置 `statement_timeout`（默认 10,000 ms，`QUERY_STATEMENT_TIMEOUT_MS` 可配），并在角色级设置同值作为兜底。超时错误映射为失败 `QY_TIMEOUT`。
- **结果行数上限与内存边界**：执行器使用 PostgreSQL 服务端（命名）游标，结果集保留在数据库侧按块传输，`execute()` 不会在 API 进程内存物化完整结果；应用侧按块取数，达到上限（默认 1,000 行，`QUERY_MAX_ROWS` 可配）即停止取数并标记截断，语义与理由见 [api.md](api.md)。
- **连接池与并发**：只读执行使用独立有界连接池（默认上限 5，`QUERY_MAX_CONCURRENCY` 可配）并与平台写入池隔离；服务层以同容量信号量闸门并发，容量满时新提交以失败 `QY_CAPACITY_EXCEEDED` 落审计（资源繁忙是执行失败，不是策略拒绝），不会产生超额连接，避免用户查询耗尽审计写入能力。连接建立失败映射为稳定失败 `QY_ANALYTICS_UNAVAILABLE`，同样不留 `running` 审计记录。
