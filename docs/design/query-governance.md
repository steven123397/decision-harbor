# 查询治理设计

## 范围

查询运行的状态机、审计事实结构、基于 SQLGlot AST 的策略规则、执行资源限制，以及拒绝与失败的语义。治理规则的产品要求以 [产品需求](../background/product-requirements.md) 的"SQL 治理规则"为准，本文给出落地结构。

## 查询运行状态

状态集合：`running`、`succeeded`、`rejected`、`failed`。后三种为终态，不可逆。

```text
创建 ──► running ──┬──► succeeded（策略允许且执行成功）
                   ├──► rejected（策略拒绝，未执行）
                   └──► failed（执行异常，含超时）
```

决策：记录统一以 `running` 创建再转终态，而不是拒绝时直接落 `rejected`。理由：单一创建路径保证被拒绝的提交同样拥有完整记录（创建时间、原始 SQL），审计语义一致。

## 审计事实

`platform.query_runs` 表，对应背景的审计要求（原始 SQL、策略判定或拒绝原因、执行状态、返回行数、耗时、错误摘要、创建时间）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `uuid` 主键 | 应用侧生成，对外记录标识。 |
| `sql_text` | `text` 非空 | 用户原始 SQL，逐字保存。 |
| `status` | `varchar(16)` 非空 | `running` / `succeeded` / `rejected` / `failed`，CHECK 约束限定。 |
| `error_code` | `varchar(64)` 可空 | 稳定机器可读码；`rejected`、`failed` 时非空。 |
| `error_message` | `text` 可空 | 可读说明或错误摘要，超长截断。 |
| `row_count` | `integer` 可空 | 实际返回行数（截断后）；`succeeded` 时非空。 |
| `duration_ms` | `integer` 可空 | `analytics` 上的执行耗时；未执行（`rejected`）为 NULL。 |
| `created_at` | `timestamptz` 非空 | 记录创建时间。 |

决策：`rejected` 的拒绝原因与 `failed` 的错误摘要共用 `error_code` / `error_message` 一对字段。理由：`status` 已区分两种语义，单一通道避免双通道字段发散；对外 API 的错误语义随 `status` 解释。

## SQLGlot AST 策略

策略模块为纯函数：输入 SQL 字符串与对象白名单配置，输出判定结果（允许，或拒绝码加说明），无任何 IO。这是单元测试接缝。解析方言固定为 PostgreSQL。

判定步骤，任一失败即拒绝：

1. 解析失败 → `POLICY_INVALID_SYNTAX`。
2. 语句数不为一 → `POLICY_MULTI_STATEMENT`。
3. 根节点必须是查询表达式（`SELECT`、`WITH ... SELECT`、`UNION` / `INTERSECT` / `EXCEPT` 及其嵌套组合）→ 否则 `POLICY_NON_QUERY_STATEMENT`。此步覆盖 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO` 等语句级入口。
4. 遍历全树，出现任何写操作节点（数据修改型 CTE、树内嵌套的 DML/DDL）或 `SELECT INTO` → `POLICY_WRITE_OPERATION`。
5. schema 限定函数（词法检测）→ `POLICY_UNSAFE_FUNCTION`。sqlglot 解析时会丢弃函数调用的 schema 限定（`pg_catalog.pg_sleep(1)` 与 `pg_sleep(1)` 得到同一 AST），因此用 token 序列补充判定 `ident.ident(` 形式；这是词法分析而非字符串匹配，字符串字面量、注释与列引用不会误判。
6. 函数允许集：只允许明确列入的只读函数（聚合、窗口、字符串、数学、条件、日期时间与 `CAST`/`EXTRACT` 等语法构造，清单见实现 `ALLOWED_FUNCTIONS`）。未知函数、表函数（`generate_series` 等）以及可解释 SQL 文本的函数（`query_to_xml` 等）一律拒绝 → `POLICY_UNSAFE_FUNCTION`。注意 sqlglot 会把方言函数名归一化（如 `date_trunc`→`TIMESTAMP_TRUNC`、`string_agg`→`GROUP_CONCAT`），允许集同时记录归一化名。
7. 对象访问范围：用词法作用域分析（`build_scope`）解析真实表引用——scope source 为表的才算真实引用，为 CTE/派生表的不计入。真实引用解析为 `(schema, table)`：无 schema 限定的按 `analytics` 解析；schema 限定必须等于 `analytics`；表名必须在五表契约白名单内。系统目录（`pg_catalog`、`information_schema`）与其余任何对象 → `POLICY_UNAUTHORIZED_OBJECT`。作用域分析失败时 fail-closed 拒绝。

理由（安全相关）：背景强制策略基于 AST 与对象访问范围而非字符串黑名单——黑名单无法枚举注释、大小写、引号、嵌套等变体。词法作用域分析修复了两个已确认缺陷：同名 CTE 不再遮蔽显式 `pg_catalog` 引用（schema 限定引用始终按真实对象校验），同时合法 CTE（含与白名单表同名的只读 CTE）不受影响；函数允许集阻断以字符串参数执行二次 SQL 的函数（如 `query_to_xml` 读取 `pg_user`），其副产品是 `pg_sleep` 等目录函数同样被拒（原先"函数级限制延后"的决策由此落地，超时改用大结果集交叉连接验证）。

## 资源限制

- 语句超时：执行会话设置 `statement_timeout`，默认 5000ms，环境变量可配置。超时（SQLSTATE `57014`）映射为 `failed` + `QUERY_TIMEOUT`。
- 行数上限：默认 1000 行，环境变量可配置。执行器流式读取，最多取上限加一行：超出则截断到上限，响应置 `truncated=true`，审计 `row_count` 记实际返回数。理由：上限的语义是约束内存与传输而非判定失败（背景要求"上限"而非"报错"）；显式截断标记优于静默丢弃。
- 两项限制值集中在 API 配置中，集成测试可下调以触发边界。

## 错误语义汇总

策略拒绝码：`POLICY_INVALID_SYNTAX`、`POLICY_MULTI_STATEMENT`、`POLICY_NON_QUERY_STATEMENT`、`POLICY_WRITE_OPERATION`、`POLICY_UNAUTHORIZED_OBJECT`、`POLICY_UNSAFE_FUNCTION`。执行失败码：`QUERY_TIMEOUT`、`EXECUTION_ERROR`（其他数据库错误）、`INTERNAL_ERROR`（未预期异常）。错误码为稳定契约，允许追加，既有码不得改义。

失败摘要的安全边界：对外（API 响应与审计记录）只含稳定摘要——`QUERY_TIMEOUT` 的固定说明，或 `EXECUTION_ERROR` 的"数据库执行错误（SQLSTATE xxxxx）"；数据库原文（含数据字面量）只写服务端日志，不进入响应与审计。

## 测试接缝

- 单元：策略纯函数的允许、拒绝、对象范围与边界语法矩阵（CTE 遮蔽、引号标识符、schema 限定、别名、子查询引用），无数据库依赖，见 [测试设计](testing.md)。
- 集成：超时与截断在真实 `analytics` 上验证。
