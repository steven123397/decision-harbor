# 首轮受治理查询链路设计

## 设计定位

本文档把 [产品需求](../background/product-requirements.md)、[技术约束](../background/technical-constraints.md) 和 [销售分析数据契约](../../datasets/sales-analytics-v1/contract.json) 中已经确认的首轮约束转化为可实现的产品内生设计。它定义模块边界、数据流、状态、接口和验证接缝；不改变背景资料中的字段、业务口径或产品范围。

首轮设计以单个本地 Compose 实例为运行边界。应用源码、依赖和运行脚本按本设计建立；本文档仍只把已确认的边界和可验证契约视为长期事实。

## 1. 范围与非目标

### 1.1 首轮范围

首轮交付形成一条可运行的显式 SQL 查询链路：

1. React 查询工作台接收用户输入的 SQL，并显示提交中、成功、拒绝和失败状态。
2. FastAPI 提供健康检查、就绪检查、查询提交和查询记录读取接口。
3. API 使用 SQLGlot 的 PostgreSQL AST 解析器执行单语句、只读查询策略和对象访问范围检查。
4. 通过独立的 `analytics_reader` 身份在 `analytics` 数据库中执行允许的查询。
5. 通过独立的 `platform_writer` 身份在 `platform` 数据库中保存查询运行记录和状态事件。
6. 使用一个 PostgreSQL 容器承载两个逻辑数据库，使用 Alembic 分别迁移，并从固定数据集契约和 CSV 幂等填充 `analytics`。
7. 提供可配置的 Compose 项目名和 Web/API 宿主端口，使多个本地工作区可以并行运行。
8. 建立 SQL 策略单元测试、双数据库集成测试和查询工作台浏览器主流程测试接缝。

### 1.2 非目标

以下内容保持背景资料定义的非目标，不因本设计扩张：

- LLM、自然语言转 SQL、RAG、MCP 和 A2A。
- 用户注册、复杂 RBAC、完整运营后台、图表编辑器和前端视觉专项。
- 用户 SQL 访问 `platform` 数据库、跨库查询或使用平台写入身份执行用户 SQL。
- 保存查询、查询历史管理、导出、取消运行、异步任务队列和实时推送。
- 通过 SQL 重写实现结果限制，或把查询结果复制到平台审计库。

首轮 API 在请求内完成查询执行。工作台在 HTTP 请求未完成时显示执行中状态；持久化状态仍然允许其他请求通过查询记录接口观察短暂的 `received` 或 `executing` 状态。这一取舍避免首轮引入队列、任务恢复和额外的交付边界。

## 2. 领域概念与不变量

### 2.1 核心概念

- **Query Run：** 一次 `POST /api/v1/query-runs` 提交对应的一条可追踪运行记录，以 UUID 标识。
- **Raw SQL：** 用户提交的原始 SQL 文本。审计保存其原值；策略解析不修改它，执行器也不把重写后的 SQL 当作原始输入。
- **Policy Decision：** `not_evaluated`、`allowed` 或 `rejected`。策略拒绝属于可预期的业务结果，不等同于 API 传输错误。
- **State：** 运行记录的生命周期状态。终态为 `succeeded`、`rejected` 或 `failed`。
- **Audit Fact：** 能从平台数据库记录确认的原始 SQL、策略判断、拒绝原因、运行状态、行数、耗时、错误摘要和创建时间。

### 2.2 状态机

```text
received
   |-- policy rejected --> rejected
   |-- policy allowed  --> executing --> succeeded
                                      \-> failed
```

状态规则如下：

| 状态 | 含义 | 允许的下一状态 |
| --- | --- | --- |
| `received` | 已保存原始 SQL 和创建事件，尚未完成策略判定。 | `rejected`、`executing`、`failed` |
| `executing` | 策略已允许，正在 `analytics` 上执行或读取结果。 | `succeeded`、`failed` |
| `succeeded` | 查询完成并且结果与终态审计事实均已保存。 | 无 |
| `rejected` | SQL 解析、语句类型、对象范围或资源前置检查未通过。 | 无 |
| `failed` | 允许后的执行、超时、结果序列化或审计持久化流程失败。 | 无 |

- `received` 和 `executing` 是短暂状态；首轮没有 `queued`、`cancelled` 或重试状态。
- `outcome` 字段只在终态存在，值与终态相同；短暂状态的 `outcome` 为 `null`。
- 策略拒绝必须在未建立 `analytics` 查询连接前完成，并记录 `policy_decision=rejected`。
- 一旦进入 `executing`，`policy_decision` 必须是 `allowed`；数据库执行失败不能改写为策略拒绝。
- 终态提交失败时不得向调用方伪造 `succeeded`。首轮记录保持 `executing`，API 返回 `503 service_not_ready`；首轮不自动恢复未闭合运行，避免在没有执行结果事实时擅自改写审计状态。

### 2.3 不变量

1. 每次进入应用层的有效 SQL 提交对应一个 `query_run`；同一个 SQL 重复提交会产生不同的运行 ID，不做隐式去重。
2. 平台审计创建成功是执行用户 SQL 的前置条件；平台数据库不可用时不连接 `analytics`。
3. 用户 SQL 只可能到达 `analytics_reader` 连接，不能复用 `platform_writer` 连接。
4. `query_runs` 不保存结果行；结果只在成功的提交响应中返回，避免把业务数据复制到平台状态库。
5. 所有策略拒绝和执行失败都使用稳定错误码；数据库原始错误不直接返回给用户。
6. 只有固定契约中的五张业务表进入首轮对象白名单，表名、字段名和业务计算口径以数据契约为准。

## 3. 模块边界与数据流

### 3.1 模块职责

| 模块 | 提供的能力 | 不负责的内容 |
| --- | --- | --- |
| Web 查询工作台 | 编辑 SQL、提交请求、显示状态、展示结果或拒绝原因。 | SQL 策略、数据库连接、结果业务计算。 |
| API 传输层 | HTTP 路由、请求校验、响应模型和统一错误包装。 | AST 遍历、迁移、数据库权限配置。 |
| Query Run Service | 编排审计创建、策略判定、执行状态转换和终态保存。 | 具体 SQL 解析和具体数据库驱动细节。 |
| SQL Policy | 使用 SQLGlot 解析单条 PostgreSQL SQL，检查 AST、对象和资源前置条件。 | 执行 SQL、写审计、决定 HTTP 状态码。 |
| Analytics Executor | 在只读事务中执行已允许 SQL，施加超时和结果上限，规范化结果值。 | 判断 SQL 是否允许、写平台审计。 |
| Platform Audit Store | 在 `platform` 中创建运行记录、追加状态事件、读取审计事实。 | 访问 `analytics` 或保存结果行。 |
| Database Bootstrap/Migration/Seed | 创建逻辑数据库和身份，运行两套迁移，校验并填充固定数据。 | 处理用户查询或平台业务请求。 |
| Compose Runtime | 组装 Web、API 和 PostgreSQL，提供实例级命名和端口配置。 | 生成全局固定容器、网络或数据卷。 |

Query Run Service 的生产入口和测试入口保持一致，最小领域接缝为：

```text
submit(sql) -> QueryRunResponse
```

它只依赖四个有明确职责的协作能力：`PolicyEvaluator.evaluate`、`AuditStore`、`AnalyticsExecutor.execute` 和可注入的时钟。这样可以测试状态顺序和失败语义，而不让 API 路由了解两个数据库的事务细节。

### 3.2 正常数据流

1. Web 将用户输入的 SQL 原文发送到 API；客户端不做 SQL 重写或黑名单过滤。
2. API 只校验 JSON 结构、字符串类型和传输大小，然后调用 Query Run Service。
3. Service 在 `platform` 创建 `received` 记录和事件，保存服务端 UTC 创建时间。
4. SQL Policy 使用 PostgreSQL 方言解析 AST，按第 5 节规则返回允许或稳定拒绝原因。
5. 若拒绝，Service 在 `platform` 中写入 `rejected` 终态和事件，不接触 `analytics`，然后返回拒绝响应。
6. 若允许，Service 在 `platform` 先提交 `executing` 状态和事件，再调用 Analytics Executor。
7. Executor 在 `analytics` 的只读事务中执行 SQL，读取至多一行超出上限的结果，完成资源检查和 JSON 值规范化。
8. Service 将成功结果或稳定失败原因写回 `platform` 终态；成功响应返回列定义、行数据、行数和耗时，失败响应不返回部分结果。
9. Web 根据响应显示结果表格、拒绝原因或失败信息，并展示运行 ID，便于读取审计事实。

两个数据库之间不使用分布式事务，也不通过 `dblink` 或用户 SQL 实现跨库访问。终态审计写入失败时，API 不把数据库执行结果宣称为成功；首轮不做自动重试，返回 `service_not_ready`，并保留可恢复的非终态记录。

## 4. 双数据库与身份边界

### 4.1 逻辑数据库

PostgreSQL 单容器提供两个逻辑数据库，数据和连接权限按数据库隔离：

| 数据库 | Schema | 内容 | 运行时访问身份 |
| --- | --- | --- | --- |
| `platform` | `platform` | 查询运行审计和状态事件。 | `platform_writer`。 |
| `analytics` | `analytics` | 契约定义的 `customers`、`product_categories`、`products`、`orders`、`order_items`。 | `analytics_reader`。 |

两套业务表的列、类型、外键、唯一约束、行数和业务规则必须逐项遵循 `datasets/sales-analytics-v1/contract.json`。设计中的 `platform` 表不是分析数据契约的一部分。

### 4.2 运行时身份

| 身份 | 允许范围 | 明确禁止 |
| --- | --- | --- |
| `platform_writer` | 仅连接 `platform`；读写 `platform.query_runs` 和 `platform.query_run_events` 所需的行。 | 连接 `analytics`、DDL、访问分析表。 |
| `analytics_reader` | 仅连接 `analytics`；对 `analytics` schema 的五张业务表拥有 `USAGE` 和 `SELECT`。 | 连接 `platform`、任何 DML/DDL、临时表、序列写入和未授权对象。 |
| `platform_readiness` | 仅连接 `platform`；读取 `platform.alembic_version` 以执行就绪检查。 | 写入审计表、连接 `analytics` 或执行迁移。 |
| `analytics_readiness` | 仅连接 `analytics`；读取 `analytics.alembic_version` 以执行就绪检查。 | 写入业务表、连接 `platform` 或执行迁移。 |
| `db_migrator` | 仅用于启动、迁移和 seed；按运行命令需要连接两个数据库。 | 注入 API 运行环境或处理用户 SQL。 |

初始化时显式撤销不需要的数据库 `CONNECT`、schema `USAGE`、表权限和默认权限。API 只接收 `platform_writer`、`analytics_reader` 和两个只读就绪身份的独立连接配置，迁移身份不进入 Web 或 API 容器的运行时配置。数据库权限是 AST 策略之外的第二道边界，但不能因此省略 AST 对系统目录和对象范围的检查。

Analytics Executor 每次查询都打开只读事务，并在连接级别固定 `search_path` 为 `analytics, pg_catalog`。未限定表名只允许按该固定路径解析到白名单表；显式限定名必须使用 `analytics` schema。`platform` 从网络连接和 SQL 对象策略两侧同时隔离。

## 5. SQLGlot AST 策略

### 5.1 解析与语句形状

策略模块接收原始 SQL 和 PostgreSQL 方言，执行以下顺序：

1. 检查 UTF-8 字节长度；空白或空字符串直接拒绝。
2. 用 SQLGlot 解析完整文本，要求解析结果恰好包含一条语句；解析异常使用 `sql_parse_error`。
3. 只接受以 `SELECT`、`WITH ... SELECT` 或由这些查询组成的 `UNION`、`INTERSECT`、`EXCEPT` 为根的 AST。
4. 遍历整个 AST，拒绝任何 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO`、`SET`、事务控制、`EXPLAIN`、`SHOW` 和其他非查询根节点。
5. 遍历所有 CTE、子查询和集合操作分支，拒绝数据修改型 CTE、`SELECT INTO`、行锁子句和任何隐藏的第二条语句。

不使用字符串黑名单作为安全判定。注释、大小写、换行和等价的 SQL 表面写法只能影响原文显示，不能绕过 AST 节点检查。

### 5.2 对象访问范围

只允许以下五个基础表对象：

```text
analytics.customers
analytics.product_categories
analytics.products
analytics.orders
analytics.order_items
```

- 每个真实表引用都必须在白名单中；`pg_catalog`、`information_schema`、临时表、序列、视图、外部表和其他 schema 均拒绝。
- 未限定的表名按固定 `search_path` 解析，但策略仍将其规范化为 `analytics.<table>` 后比对白名单。
- CTE 名、表别名、派生表和列别名是查询内部名称，不被误判为基础对象；其内部查询仍必须递归检查。
- 连接、子查询、聚合、窗口、`GROUP BY`、`HAVING`、排序、分页和集合操作可以使用，但每一条基础对象路径都必须通过相同检查。
- `SELECT *` 可以保留，由结果列数和响应大小限制兜底；策略不通过改写 SQL 来补充 `LIMIT`。

### 5.3 函数、类型和表达式

首轮函数策略采用显式允许、未知即拒绝：

- 聚合函数：`COUNT`、`SUM`、`AVG`、`MIN`、`MAX`、`STRING_AGG`。
- 窗口函数：`ROW_NUMBER`、`RANK`、`DENSE_RANK`、`LAG`、`LEAD`。
- 空值和条件函数：`COALESCE`、`NULLIF`、`GREATEST`、`LEAST`。
- 数值、文本和日期函数：`ROUND`、`ABS`、`CEIL`、`FLOOR`、`LOWER`、`UPPER`、`LENGTH`、`SUBSTRING`、`TRIM`、`DATE_TRUNC`、`DATE_PART`、`EXTRACT`。
- `CASE`、比较、算术、布尔、`IN`、`EXISTS`、子查询和标准类型转换按 AST 节点检查。

函数名不允许通过用户 schema 或显式动态解析；未知函数、扩展函数、网络/文件访问函数、会话修改函数、睡眠函数和动态 SQL 函数均拒绝。`CAST` 的目标类型只允许 `numeric`、`decimal`、`integer`、`bigint`、`text`、`date`、`timestamp` 和 `timestamptz` 等安全标量类型；对象标识符类型（如 `regclass`、`regrole`、`regproc`、`regnamespace`、`regtype`）、数组类型和用户自定义类型拒绝。这样既保留销售分析需要的聚合、日期和金额表达式，也避免通过类型解析间接触碰系统对象。

### 5.4 资源限制

首轮默认值如下。所有值由服务端配置，用户 SQL、请求参数和 `SET` 语句不能覆盖；实现必须为每个值提供边界测试。

| 限制 | 默认值 | 施加位置与失败语义 |
| --- | --- | --- |
| HTTP 请求体 | 128 KiB | 超过传输上限返回 `413 request_too_large`，因未进入应用层而不创建运行记录。 |
| SQL 原文 | 64 KiB UTF-8 字节 | 创建运行记录后拒绝为 `sql_too_large`，保存原文和审计事实。 |
| 结果列数 | 128 | 结果读取后超过上限，终止为 `result_shape_too_large`。 |
| 结果行数 | 10,000 | Executor 最多读取 10,001 行；发现超出上限即终止为 `result_limit_exceeded`，不返回部分结果。 |
| 序列化结果 | 4 MiB | JSON 规范化后超过上限，终止为 `result_payload_too_large`。 |
| PostgreSQL 语句 | 5,000 ms | 连接上使用 `statement_timeout`，终止为 `query_timeout`。 |
| PostgreSQL 锁等待 | 1,000 ms | 使用 `lock_timeout`，终止为 `lock_timeout`。 |
| 数据库连接 | 2,000 ms | 使用连接超时，终止为 `analytics_unavailable` 或 `service_not_ready`。 |
| 同时执行数 | 每个 API 进程 4 | 使用有界信号量；无法取得执行槽时终止为 `executor_busy`。 |

结果上限通过受控游标读取，不在用户 SQL 外层包裹或追加 `LIMIT`，避免改变排序、窗口和集合语义。AST 可以对字面量 `LIMIT` 做前置比较，超过 10,000 时直接使用 `result_limit_exceeded` 拒绝；非字面量或实际结果超限则由 Executor 读取一行超额结果后失败。查询在超出行数或响应大小时回滚只读事务，部分行不成为成功结果。

## 6. 查询运行记录与审计事实

### 6.1 `platform` 数据模型

`platform.query_runs` 保存每次运行的当前聚合事实，建议字段如下：

| 字段 | 约束与含义 |
| --- | --- |
| `id` | UUID 主键，运行记录标识。 |
| `raw_sql` | 非空 `text`，保存用户原始 SQL。 |
| `state` | 受约束的 `received`、`executing`、`succeeded`、`rejected`、`failed`。 |
| `outcome` | 终态重复记录状态，短暂状态为 `NULL`，便于 API 读取。 |
| `policy_decision` | `not_evaluated`、`allowed` 或 `rejected`。 |
| `policy_version` | 执行决策的策略版本标识，便于规则变化后的审计解释。 |
| `policy_code` | 拒绝时的稳定策略码，允许或未判定时为 `NULL`。 |
| `created_at` | 服务端 UTC 创建时间，非空。 |
| `started_at` | 进入 `executing` 的时间，拒绝运行可为空。 |
| `finished_at` | 进入终态的时间，短暂状态可为空。 |
| `duration_ms` | 从创建到终态的端到端耗时；未完成时为空。 |
| `execution_duration_ms` | `analytics` 执行阶段耗时；策略拒绝时为空。 |
| `row_count` | 成功结果的行数，空结果为 `0`；失败或拒绝为空。 |
| `error_code` | 失败或拒绝的稳定错误码。 |
| `error_summary` | 有界、脱敏、面向用户的错误摘要，不保存数据库堆栈。 |

`platform.query_run_events` 保存状态变化的追加事件：`run_id`、递增的 `sequence`、`state`、`event_type`、`occurred_at`、可选的稳定 `code`。聚合记录和事件在同一 `platform` 事务中更新。事件不复制结果行，错误详情只保存必要的稳定摘要。

### 6.2 审计写入顺序与恢复

- 创建运行记录和 `received` 事件必须先提交。
- 策略拒绝在一个事务中写入 `policy_decision`、`policy_code`、`rejected` 状态和终态事件。
- 允许执行时，先提交 `executing` 状态和事件，再打开 `analytics` 事务。
- 成功或失败的终态、耗时、行数或错误摘要与对应事件在一个事务中提交。
- 每次状态事务先锁定对应的 `query_runs` 行并校验当前状态，只允许状态机定义的转移；终态更新必须是幂等的，不能被并发请求覆盖。
- 不使用跨数据库两阶段提交。若终态事务失败，Service 进行有限次数的幂等更新重试；仍失败时不返回成功结果，并由启动恢复将超过阈值的 `executing` 记录标记为 `failed/audit_recovery`，说明最终执行结果无法从平台事实确认。

平台数据库故障会使新查询在创建审计记录阶段失败。`/ready` 必须在平台或分析数据库不可用、迁移未到目标版本时返回失败，防止服务继续接受无法完整审计的查询。

## 7. 最小 API 与错误语义

### 7.1 路由

| 方法 | 路径 | 成功语义 |
| --- | --- | --- |
| `GET` | `/health` | 进程存活即返回 `200`，不把数据库依赖混入 liveness。 |
| `GET` | `/ready` | 两个数据库可连接且迁移就绪时返回 `200`；否则返回 `503`。 |
| `POST` | `/api/v1/query-runs` | 创建并同步完成一条运行记录，返回统一的成功、拒绝或失败结果。 |
| `GET` | `/api/v1/query-runs/{id}` | 返回指定运行的状态和审计事实；未知 ID 返回 `404`。 |

### 7.2 查询响应

提交接口的请求体只有一个字段：

```json
{
  "sql": "SELECT ..."
}
```

应用层接收后不自动 trim 或改写 `sql`。成功结果使用统一结构：

```json
{
  "id": "2c5f5d33-2d44-4c74-b1d8-f0a2ec25f76d",
  "raw_sql": "SELECT region FROM analytics.customers LIMIT 1",
  "state": "succeeded",
  "outcome": "succeeded",
  "created_at": "2026-08-14T10:00:00Z",
  "policy": {"decision": "allowed", "code": null},
  "result": {
    "columns": [{"name": "region", "type": "text"}],
    "rows": [["North"]],
    "row_count": 1,
    "duration_ms": 42
  },
  "error": null
}
```

拒绝和失败仍返回同一顶层结构，`result` 为 `null`，`error` 包含稳定 `code`、可读 `message` 和运行 ID 已在顶层提供。`GET` 主要返回审计事实和状态，不返回持久化结果行；成功结果只在提交响应中存在。

HTTP 状态码只表达传输或资源访问问题，不把正常的策略拒绝和已记录的执行失败变成不同的响应模型：

- `200`：运行记录已经创建并到达 `succeeded`、`rejected` 或 `failed`。
- `400 invalid_request`：JSON 结构错误、缺少 `sql` 或类型错误，未创建运行记录。
- `404 query_run_not_found`：指定 ID 不存在。
- `413 request_too_large`：HTTP 请求体在进入应用层前超过传输上限。
- `503 service_not_ready`：平台审计前置条件、数据库连接或迁移就绪条件不满足。

首轮稳定错误码至少包括：

| 错误码 | 类型 | 说明 |
| --- | --- | --- |
| `sql_parse_error` | rejected | PostgreSQL AST 无法解析。 |
| `multiple_statements` | rejected | 输入包含多条语句。 |
| `non_read_query` | rejected | 根节点或子节点包含非只读语句。 |
| `write_cte` | rejected | CTE 内存在写操作。 |
| `select_into` | rejected | 查询尝试写入新对象。 |
| `object_not_allowed` | rejected | 访问非白名单 schema、表或系统对象。 |
| `function_not_allowed` | rejected | 函数或类型转换不在显式允许范围。 |
| `sql_too_large` | rejected | SQL 原文超过策略输入上限。 |
| `result_shape_too_large` | failed | 结果列数超过上限。 |
| `result_limit_exceeded` | failed | 结果行数超过上限。 |
| `result_payload_too_large` | failed | JSON 结果超过响应大小上限。 |
| `query_timeout` | failed | 超过 PostgreSQL 语句时间上限。 |
| `lock_timeout` | failed | 锁等待超过上限。 |
| `analytics_unavailable` | failed | 分析数据库连接或执行前置条件失败。 |
| `analytics_execution_error` | failed | 允许的查询发生其他运行时错误。 |
| `result_serialization_error` | failed | 数据库值无法按响应契约规范化。 |
| `executor_busy` | failed | 已达到并行执行上限。 |

数据库驱动的详细错误只进入受控服务日志；返回给用户的 `message` 和 `error_summary` 必须有界并去除连接、SQL、路径和堆栈细节。金额和其他定点数使用字符串序列化，`bigint` 使用字符串避免 JavaScript 精度损失，时间使用 UTC ISO 8601 字符串，其余值使用 JSON 原生类型或 `null`。

## 8. 查询工作台

工作台是单页最小查询工作台，不承担运营后台或视觉专项：

- 提供带可访问标签的多行 SQL 输入区和提交按钮；提交的文本保持原样。
- 提交期间禁用重复提交并显示 `executing`，请求结束后根据 `outcome` 切换成功、拒绝或失败视图。
- 成功视图显示列名、数据库类型、行数据、行数、耗时和运行 ID；空结果显示明确的零行状态。
- 拒绝视图显示稳定错误码、可读原因和运行 ID，不显示数据库内部细节。
- 失败视图显示稳定错误码和重试提示，但首轮没有客户端自动重试，避免重复执行产生新的审计记录。
- 页面刷新后可通过运行 ID读取状态和审计事实，但不要求平台保存结果行。

工作台只依赖 API 响应模型；SQLGlot 策略和数据库身份不能下沉到浏览器。浏览器测试通过真实 API 和固定数据集验证成功查询、策略拒绝、执行中显示和失败消息的主流程。

## 9. 迁移与幂等 seed

### 9.1 迁移边界

- `platform` 和 `analytics` 使用独立的 Alembic migration context 和版本表，任何一个数据库未到目标 head 都不算就绪。
- `platform` 迁移创建 `platform` schema、`query_runs`、`query_run_events`、检查约束、索引和必要的审计权限。
- `analytics` 迁移创建 `analytics` schema 及契约定义的五张表、主键、外键、唯一约束、金额定点数类型和数据约束。
- 迁移只能实现背景资料和数据契约已有的结构；字段重命名、删除、业务口径重解释和把订单总额加入数据表均不允许。
- Alembic 版本检查使重复迁移成为无操作；迁移失败必须回滚当前数据库事务并阻止就绪。

### 9.2 幂等 seed

seed 只使用 `contract.json`、提交的 CSV 和 `manifest.json`，不重新生成或重新解释业务数据。执行顺序遵守外键关系：`product_categories`、`customers`、`products`、`orders`、`order_items`。

seed 在单个 `analytics` 事务中遵守以下规则：

1. 空的五张表按 CSV 和契约顺序加载，并在事务提交前校验 manifest 中的契约哈希、文件哈希和行数；数据库约束继续校验关联、业务范围和唯一性，固定文件哈希同时固定其状态分布和日期范围。
2. 已存在完整且哈希与 manifest 一致的固定数据时，校验通过并安全退出，不重复插入。
3. 发现部分数据、行数不符、哈希不符或契约冲突时，回滚并返回 `seed_conflict`，不自动 `TRUNCATE`、删除或覆盖用户数据。
4. seed 的写入身份只在初始化/seed 命令中使用，API 的 `analytics_reader` 永远不能执行 seed。

这种“空库加载、完整库校验、冲突即停”的策略提供可重复启动能力，同时避免用破坏性清理掩盖数据漂移。固定 CSV 是权威公开 fixture，生成器和 manifest 只用于验证可重复性。

## 10. Compose 并行隔离

Compose 设计以项目名作为每个工作区的命名空间：

- 通过 `COMPOSE_PROJECT_NAME` 或等价的 `docker compose -p` 注入实例名，不能写死项目名。
- 服务保持普通 Compose 服务发现名称（如 `web`、`api`、`postgres`），不设置 `container_name`。
- 网络、PostgreSQL 数据卷和其他资源使用 Compose 项目作用域生成，不使用全局固定名称、全局网络或共享绑定目录。
- `WEB_HOST_PORT` 和 `API_HOST_PORT` 可配置并只绑定当前实例；PostgreSQL 不要求暴露固定宿主端口，API 通过 Compose 内部服务名连接。
- 不同工作区使用不同的 Compose 项目名和宿主端口，启动、停止和销毁命令只能操作当前项目名对应的资源。
- 运行时凭据和端口通过环境配置或本地未提交文件注入，不写入仓库背景资料和固定数据文件。

统一开发入口为 `./dev`：`up` 按 Compose 依赖启动 PostgreSQL、迁移/幂等 seed、API 和 Web，`down` 停止当前项目，`destroy` 删除当前项目及数据卷，`test` 运行数据校验、容器内 API 测试和 Web 测试。Compose 健康检查和 API `/ready` 共同作为运行就绪门槛。

## 11. 测试接缝

### 11.1 SQL 策略单元测试

策略测试直接调用 `evaluate_sql`，不启动数据库，覆盖：

- 单表、连接、子查询、聚合、窗口和 `UNION`、`INTERSECT`、`EXCEPT` 的允许路径。
- 多语句、所有背景列出的写操作、写 CTE、`SELECT INTO`、行锁、系统目录、非白名单表和 schema 的拒绝路径。
- CTE 递归遍历、别名、注释、大小写、引号、函数 allowlist、对象标识符类型转换和未知函数。
- SQL 字节长度、列数预估、显式过大 `LIMIT`、不允许的 `SET` 和稳定错误码。

### 11.2 双数据库集成测试

集成测试使用真实 PostgreSQL 容器或临时实例，验证而不是模拟权限：

- 两个逻辑数据库、两套 migration head 和契约五表结构均能建立。
- `platform_writer` 不能连接 `analytics`；`analytics_reader` 不能连接 `platform`，也不能写分析表。
- 允许的 SQL 只能读取固定分析表；应用审计必须写入 `platform`。
- 空库 seed、重复 seed、完整 fixture 校验和部分/冲突 fixture 停止行为符合第 9 节。
- `QueryRunService.submit` 的审计创建先于分析执行，状态事件顺序正确；超时、权限拒绝、数据库不可用和终态审计失败均得到稳定结果。
- `/ready` 对数据库不可用、迁移未完成和两库均就绪的状态分别返回正确结果。

### 11.3 Web 与浏览器测试

- Vitest 测试工作台的提交中、成功、拒绝、失败、空结果和响应错误状态，不复制后端策略实现。
- Playwright 在独立 Compose 项目中输入允许的销售分析 SQL，验证执行中提示、结果列/行和审计 ID。
- Playwright 输入非白名单或写操作 SQL，验证页面显示拒绝码和原因，且 API 记录可通过 ID 读取。
- 服务单元测试覆盖执行失败和稳定错误语义；首轮浏览器主链覆盖成功结果和对象越权拒绝，超时和结果上限由策略/执行器单元接缝继续覆盖。

测试使用与生产相同的 `QueryRunService.submit`、API 响应模型和 Compose 隔离入口；只在纯策略和存储故障测试中替换明确的协作者，不建立第二套测试专用业务流程。

## 12. 关键决策、失败处理与延后决策

### 12.1 关键决策与理由

1. **首轮同步执行。** 背景只要求最小查询链路，没有队列或异步基础设施；请求内执行减少任务恢复、取消和重复投递的复杂度，工作台仍能通过提交中状态反馈执行过程。
2. **审计先于分析执行。** 没有平台记录就不执行用户 SQL，避免产生无法追踪的分析访问；两个数据库不做分布式事务，终态不确定时明确保留未闭合状态。
3. **AST allowlist 加数据库权限。** SQLGlot 能检查语句形状和对象路径，数据库身份能阻止应用策略失误后的写入；两者分别解决语法绕过和凭据越权问题。
4. **不重写 SQL。** 原文审计、窗口/集合语义和数据库错误更容易对应；结果上限由受控读取实现，不改变用户查询的逻辑。
5. **不持久化结果行。** 结果可能包含业务敏感数据，平台数据库只保存审计事实可以维持双库边界并控制状态库增长。
6. **冲突 seed 停止而不是清空重建。** 固定 CSV 是权威 fixture，自动删除会掩盖数据漂移并破坏本地状态；显式冲突更容易诊断和恢复。
7. **Compose 资源完全项目化。** 并行工作区必须互不抢占容器、端口、网络和卷；项目名是 Compose 原生且可验证的隔离边界。

### 12.2 失败处理边界

- 输入结构错误发生在运行记录创建前，只返回 API 输入错误。
- SQL 策略错误发生在 `analytics` 连接前，终态为 `rejected`。
- 分析数据库连接、超时、权限、资源限制和序列化错误发生在允许之后，终态为 `failed`，保留 `policy_decision=allowed`。
- 平台初始写入失败时拒绝执行；平台终态写入失败时不承诺成功结果，并保留恢复线索。
- 所有终态写入使用单库事务和状态检查，重复终态更新必须幂等，不允许把 `succeeded` 改回其他状态。

### 12.3 延后决策

以下内容不属于当前设计的未决缺口，而是明确延后到需求扩展时重新设计：

- 用户身份、细粒度 RBAC、租户隔离和按用户授权对象。
- 异步任务队列、取消、重试、SSE/WebSocket 和多副本执行协调。
- 查询结果持久化、导出、保存查询、历史搜索和审计保留周期。
- 更完整的 PostgreSQL 函数 allowlist、视图/语义层和跨数据集访问。
- 生产部署、备份恢复、密钥轮换和多环境发布拓扑。
