# 受治理查询链路

## 范围

本文给出 DecisionHarbor 首轮可实现设计：显式 SQL 从查询工作台进入，经 AST 策略判定，由分析只读身份在 `analytics` 执行，审计事实写入 `platform`，并通过最小 HTTP 接口返回。

本文覆盖模块与数据流、查询运行状态、双库身份、SQLGlot 策略与资源限制、API 与错误语义、查询工作台、迁移与幂等 seed、Compose 并行隔离，以及单元、集成和浏览器测试接缝。

本文不覆盖阶段切片或实现顺序。产品目标、非目标、允许/拒绝的 SQL 类别、最小端点路径和固定技术栈以 [产品需求](../background/product-requirements.md) 与 [技术约束](../background/technical-constraints.md) 为准。五张分析表的字段、关联和业务口径以 [数据契约](../../datasets/sales-analytics-v1/contract.json) 为准，本文不重新解释。

## 目标 / 非目标

- 目标：交付一条可在干净 WSL + Docker Compose 环境启动的受治理查询链路；策略、执行、审计和工作台使用同一套查询运行语义。
- 目标：把背景中的双库隔离、AST 策略、资源限制和三类测试落到明确模块边界与公共接缝。
- 非目标：LLM、自然语言转 SQL、RAG、MCP、A2A、用户注册、复杂 RBAC、运营后台、图表编辑器、查询历史浏览器。
- 非目标：跨库访问平台状态，或使用平台身份执行用户 SQL。
- 非目标：改名、删除或重新解释契约字段与已实现销售额口径。

## 术语与场景

规范术语见 [CONTEXT.md](CONTEXT.md)。本文只补充场景，不引入第二套词汇。

1. 业务人员在查询工作台提交一条只读 `SELECT`，策略允许后返回结果表，`platform` 留下成功审计事实。
2. 提交包含多语句、写操作、未授权对象或未允许函数的 SQL，策略拒绝，不访问 `analytics` 业务数据，审计状态为 `rejected`。
3. 合法 SQL 在执行中超时或超过行数上限，审计状态为 `failed`，工作台展示稳定错误码与说明。
4. 重复启动同一 Compose 实例时，迁移与 seed 不产生重复行；另一工作区用不同项目名和宿主端口并行运行，互不抢占网络或数据卷。

## 不变量

- 用户 SQL 只在策略允许之后，由分析只读身份在 `analytics` 上执行。平台身份与分析所有者身份从不执行用户 SQL。
- 每个含字符串 `sql` 的合法请求体都产生一条查询运行，包括随后被策略拒绝或因超长被拒的提交；请求体无法解析为 `{ "sql": string }` 时不建记录。
- 查询运行一旦进入 `succeeded`、`rejected` 或 `failed`，不得改写为其他状态，也不得删除审计事实。
- 分析表结构与 CSV 口径与契约一致；seed 只加载已提交的权威 CSV，不在启动时重新生成数据。
- 本地实例不得固定 `container_name`、Docker 网络名、数据卷名、绑定目录或数据库宿主端口。
- 金额与折扣按契约使用定点数；JSON 中的 `numeric` 值序列化为字符串，避免 IEEE 754 丢失精度。

## 模块边界

仓库按三个可部署单元划分，调用方只依赖各自的最小接口。

| 单元 | 负责 | 不负责 |
| --- | --- | --- |
| `web/` | 查询工作台、把浏览器请求代理到 API | 策略、审计持久化、直连数据库 |
| `api/` | HTTP、查询运行编排、策略、只读执行、迁移与 seed | 前端视觉、运营功能 |
| `postgres` | 两个逻辑库与身份的权限边界 | 应用层 AST 策略 |

`api/` 内部模块对外只暴露下列接缝；测试与生产走同一入口。

### 查询策略

- 输入：原始 SQL 字符串。
- 输出：`allow`，或 `deny(code, message)`。
- 隐藏：SQLGlot 方言、AST 遍历、CTE 词法作用域、函数允许名单。
- 不负责：执行、超时、写审计、改写用户 SQL。

### 查询执行器

- 输入：已放行的原始 SQL。
- 输出：列定义、行、行数、耗时；或执行失败（超时、行数上限、数据库错误）。
- 隐藏：分析只读连接、`statement_timeout`、取数游标、类型到 JSON 的转换。
- 不负责：再次做完整策略判定、写 `platform`。

### 查询运行服务

- 输入：原始 SQL，或已有查询运行标识。
- 输出：查询运行的对外表示。
- 隐藏：先写 `running`、调用策略与执行器、回写终态的顺序。
- 不负责：HTTP 序列化细节以外的传输层状态码决策（由路由按本文 API 语义映射）。

### 平台存储、分析所有权存储与分析只读存储

- 平台存储只给查询运行服务使用，连接平台身份。
- 分析所有权存储只给启动期迁移与 seed 使用，连接分析所有者身份。
- 分析只读存储只给查询执行器使用，连接分析只读身份。
- 三者不得共享 Engine、会话或可切换角色的连接。

## 数据流

```text
浏览器
  → 查询工作台（展示 running）
  → POST /api/v1/query-runs
  → 查询运行服务：写入 running 审计行
  → 查询策略（SQLGlot AST + 对象范围）
        ├─ deny → 回写 rejected → 返回
        └─ allow → 查询执行器（分析只读身份 + 超时 + 行数上限）
              ├─ 成功 → 回写 succeeded（含行数与耗时）→ 返回列与行
              └─ 失败 → 回写 failed → 返回错误码与摘要
```

`GET /api/v1/query-runs/{id}` 只读 `platform` 上的审计事实，不回放 SQL，不访问 `analytics`。

工作台与 API 同源：Web 容器把 `/api`、`/health`、`/ready` 反代到 API 服务。浏览器只使用可配置的 Web 宿主端口，不直连 API 宿主端口，也不连接数据库。

## 查询运行状态和审计事实

### 状态

| 状态 | 含义 | 是否终态 |
| --- | --- | --- |
| `running` | 记录已创建，策略或执行尚未结束 | 否 |
| `succeeded` | 策略允许且执行得到结果集 | 是 |
| `rejected` | 策略拒绝；用户 SQL 未在 `analytics` 执行 | 是 |
| `failed` | 策略允许后执行失败 | 是 |

`POST` 在同一请求内同步跑完策略与执行，响应体始终是终态。工作台的「执行中」来自请求未返回时的界面状态；`running` 是为了让崩溃或并发 `GET` 仍能看到已建档的查询运行。进程在回写终态前退出时，记录可以停留在 `running`。首轮不做自动回收。

每次提交创建新的查询运行，不按 SQL 文本去重。同一语句提交两次是两次审计事件。

### 持久化字段

`platform.query_runs` 只存审计事实，不存结果行：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID，作为外部标识 |
| `sql_text` | 用户提交的原始文本，不做规范化改写 |
| `status` | `running` / `succeeded` / `rejected` / `failed` |
| `error_code` | 拒绝或失败时的稳定错误码；成功为空 |
| `error_message` | 可读摘要；成功为空 |
| `row_count` | 成功时为返回行数；否则为空 |
| `duration_ms` | 从策略开始到得出终态的毫秒数 |
| `created_at` | 创建时间，`timestamptz` |

`GET` 返回上表全部审计事实，不含结果网格。结果只出现在产生该结果的 `POST` 响应里。查询工作台不是历史结果浏览器；不把结果写入 `platform`，避免审计库膨胀，也避免只读查询的结果被当成平台主数据。

## 双数据库与只读身份边界

单一 PostgreSQL 容器提供两个逻辑数据库。初始化脚本（容器首次建库时执行）创建身份并收回默认公开权限：

| 身份 | `platform` | `analytics` |
| --- | --- | --- |
| 平台身份 | `CONNECT`；对 `query_runs` 可读写 | 无 `CONNECT` |
| 分析所有者身份 | 无 `CONNECT` | `CONNECT`；拥有 schema `analytics`；可对契约五张表做 DDL 与 seed 写入 |
| 分析只读身份 | 无 `CONNECT` | `CONNECT`；`USAGE` 于 schema `analytics`；仅对契约五张表 `SELECT` |

补充约束：

- 两个库都 `REVOKE ALL ON SCHEMA public FROM PUBLIC`，并限制 `PUBLIC` 的 `CONNECT`。
- 分析只读身份的 `search_path` 固定为 `analytics`，不能写、不能建临时表、不能执行用户定义函数。
- 分析表建在 `analytics` 库的 `analytics` schema 中，与契约的 `schema` 字段一致。
- API 使用三套独立连接配置。执行用户 SQL 时不得 `SET ROLE`，不得切到平台身份或分析所有者身份。
- 分析所有者身份只在进程启动的迁移与 seed 阶段使用，就绪后查询路径不得再持有其会话。它不属于查询执行器。
- 数据库权限是第二道边界，不能代替 AST 策略；策略也不能因为有只读身份就省略对象范围检查。

## SQLGlot AST 策略和资源限制

策略使用 SQLGlot，方言为 PostgreSQL。判定只依据解析后的 AST 与作用域内的对象引用，不依据关键字字符串黑名单。允许执行的 SQL 仍是用户原文；策略不重写语句后再执行，以免改变注释、字面量或 PostgreSQL 与 SQLGlot 生成结果之间的语义差。解析失败则拒绝，不把原文交给执行器。

### 语句形态

- `sqlglot.parse` 必须得到恰好一条表达式；空输入、仅注释、分号后残留语句均拒绝。
- 根节点必须是只读查询：`SELECT`（含 `WITH`）或 `UNION` / `INTERSECT` / `EXCEPT`。
- 拒绝背景已列出的写操作、DDL、`COPY`、`CALL`、`DO`、修改型 CTE、`SELECT INTO`，以及 `EXPLAIN`、`SHOW`、`SET`、`VACUUM`、`LOCK`、`SELECT ... FOR UPDATE/SHARE`。
- 允许连接、子查询、聚合、窗口函数，以及背景已允许的集合运算。

### 对象访问范围

遍历 AST，收集表、schema、catalog 引用，并用 CTE 词法作用域区分真实关系与 CTE 名称：

- 允许的真实表名仅为契约中的五张：`customers`、`product_categories`、`products`、`orders`、`order_items`。
- 允许的 schema 为省略或 `analytics`。`public`、`pg_catalog`、`information_schema` 及其他 schema 中的关系一律拒绝。
- 允许的 catalog 为省略或 `analytics`。
- CTE 名称只在其可见作用域内视为非物理表；CTE 内部仍按同一规则检查真实对象。
- 标识符按 PostgreSQL 规则折叠到小写后再与允许名单比较。引用未允许对象即拒绝，即使只读身份随后也会失败。

### 函数与可调用对象

函数采用允许名单，未列入即拒绝。`CAST` / `::` 作为类型转换表达式允许。下列函数允许出现在 `pg_catalog` 或未限定 schema 中；其他 schema 限定一律拒绝。

- 聚合：`count`、`sum`、`avg`、`min`、`max`
- 窗口：`row_number`、`rank`、`dense_rank`、`lag`、`lead`、`first_value`、`last_value`、`ntile`
- 空值：`coalesce`、`nullif`
- 数值：`abs`、`round`、`ceil`、`ceiling`、`floor`、`mod`、`greatest`、`least`、`sign`、`trunc`
- 文本：`concat`、`length`、`lower`、`upper`、`trim`、`ltrim`、`rtrim`、`substring`、`replace`、`left`、`right`
- 日期：`date_trunc`、`date_part`、`extract`、`age`、`now`、`current_date`、`current_timestamp`、`timezone`、`to_char`、`to_date`、`to_timestamp`

`generate_series`、`pg_sleep`、文件与大对象函数、`dblink`、`current_setting` 不在名单中。允许名单的理由是：PostgreSQL 内置可调用对象远多于背景点名的语句类别，拒绝名单无法穷尽读文件、网络和外泄会话信息的路径。

### 资源限制

| 限制 | 默认 | 作用点 |
| --- | --- | --- |
| SQL 最大字符数 | 20_000 | 查询运行服务在调用策略解析之前 |
| 语句超时 | 5_000 ms | 分析只读会话的 `statement_timeout` |
| 结果行数上限 | 1_000 | 执行器最多取 `上限 + 1` 行 |

超过字符数：`rejected` / `QUERY_TOO_LARGE`，不解析。超时：`failed` / `EXECUTION_TIMEOUT`。取到超过上限的一行：丢弃结果网格，记 `failed` / `RESULT_LIMIT_EXCEEDED`。不把截断结果标为 `succeeded`，以免调用方把部分行当成完整答案。默认值面向契约规模的本地演示数据；可通过环境变量覆盖，供测试使用，不改变语义。

## 最小 API 与错误语义

端点路径与职责保持背景定义：`GET /health`、`GET /ready`、`POST /api/v1/query-runs`、`GET /api/v1/query-runs/{id}`。

### 统一查询运行表示

```json
{
  "id": "uuid",
  "status": "succeeded",
  "sql": "SELECT ...",
  "result": {
    "columns": [{"name": "region", "type": "varchar"}],
    "rows": [["East"]],
    "row_count": 1
  },
  "error": null,
  "duration_ms": 12,
  "created_at": "2026-08-13T00:00:00+00:00"
}
```

规则：

- `status` 为 `rejected` 或 `failed` 时 `result` 为 `null`，`error` 为 `{ "code", "message" }`。
- `GET` 的 `result` 始终为 `null`；调用方以审计事实为准。
- `numeric` / `decimal` 单元格为字符串；整数为 JSON number；时间戳为 ISO 8601；布尔为 JSON boolean。
- 行是与 `columns` 对齐的数组，不使用对象，以免重复列名时丢失值。

### HTTP 映射

| 情况 | HTTP | 体 |
| --- | --- | --- |
| 进程存活 | 200 `/health` | `{ "status": "ok" }` |
| 两库可连且迁移、seed 已完成 | 200 `/ready` | `{ "status": "ready" }` |
| 未就绪 | 503 `/ready` | `{ "status": "not_ready" }` |
| 查询运行终态（含拒绝与执行失败） | 200 | 统一查询运行表示 |
| 请求体不是带字符串 `sql` 的 JSON | 400 | `{ "error": { "code": "REQUEST_INVALID", "message": "..." } }` |
| `GET` 标识不存在或不是 UUID | 404 | `{ "error": { "code": "QUERY_RUN_NOT_FOUND", "message": "..." } }` |

查询业务结果放在 200 体里的 `status`，不把 `rejected` 映射为 422、把 `failed` 映射为 500。理由：拒绝和执行失败是已记录的领域结果，工作台与 `GET` 必须用同一表示读取；HTTP 5xx 留给基础设施故障。

### 稳定错误码

| 码 | 出现条件 | 记录状态 |
| --- | --- | --- |
| `REQUEST_INVALID` | 缺字段、类型错误、非法 JSON | 无记录 |
| `QUERY_INVALID` | 无法解析为恰好一条只读查询 | `rejected` |
| `QUERY_TOO_LARGE` | 超过字符上限 | `rejected` |
| `POLICY_DENIED` | 语句形态、对象范围或函数不允许 | `rejected` |
| `EXECUTION_TIMEOUT` | 语句超时 | `failed` |
| `RESULT_LIMIT_EXCEEDED` | 超过行数上限 | `failed` |
| `EXECUTION_ERROR` | 其他执行期数据库错误；`message` 为摘要，不含连接串或身份 | `failed` |
| `QUERY_RUN_NOT_FOUND` | `GET` 未命中 | 无新记录 |

## 查询工作台

单页界面，技术栈为 React 19、TypeScript、Vite。

- 提供 SQL 输入区与提交控件。
- 提交后、响应返回前展示执行中状态；禁用重复提交。
- 成功展示列名与数据表；`rejected` / `failed` 展示 `error.code` 与 `error.message`。
- 通过同源路径调用 `POST /api/v1/query-runs`，不实现登录、查询列表、导出、图表或主题系统。
- Vitest 覆盖状态到展示的分支；不在单元测试里复制策略规则。

## 迁移与幂等 seed

- `platform` 与 `analytics` 使用独立的 SQLAlchemy MetaData 与 Alembic 版本表，互不共享迁移历史。
- `platform` 迁移只建立 `query_runs`。
- `analytics` 迁移按契约建立 schema `analytics` 与五张表、主键、唯一约束和外键；不增加订单总额冗余列；金额列为定点数。
- seed 只读取 `datasets/sales-analytics-v1/data/` 下已提交 CSV，按外键顺序加载。不调用 `generate.py` 作为运行期数据源。
- 重复 seed 使用「清空分析表后按 CSV 再装入」：`TRUNCATE ... RESTART IDENTITY CASCADE` 再 `COPY`/批量插入。权威数据是固定夹具，重装是恢复夹具，不是追加。禁止只 `INSERT` 而不清空，以免行数翻倍。
- `platform` 迁移以平台身份运行。`analytics` 迁移与 seed 以分析所有者身份运行；表创建后把五张表的 `SELECT` 授予分析只读身份。
- API 进程启动时依次：等待数据库可连、升级两库迁移、执行 seed、再接受流量。`/ready` 在上述完成且平台身份能读 `query_runs`、分析只读身份能读五张表之后才成功。
- seed 失败则进程不就绪，不得对外返回查询成功。

## Compose 并行隔离

根目录提供 Compose 编排，Web、API、PostgreSQL 同项目运行。

- `COMPOSE_PROJECT_NAME`、Web 宿主端口、API 宿主端口由环境变量配置；提交 `.env.example`，不提交密钥。
- 不写 `container_name`，不写死网络名、卷名或宿主绑定目录。卷由项目命名空间管理。
- PostgreSQL 不发布固定宿主端口；API 与迁移通过服务名访问。集成测试加入同一 Compose 网络，不依赖 `localhost:5432`。
- 统一启动入口完成构建、启动三服务、迁移、seed，并等待 `/ready` 与 Web 可访问。
- 统一测试入口在同一隔离项目中运行策略单元测试、双库集成测试、工作台 Vitest 与 Playwright 主流程。
- 两个工作区只要项目名和宿主端口不同，就可以并行使用独立容器、网络和数据卷。

## 关键决策

1. **`POST` 同步执行，结果只在该响应中返回。**  
   理由：背景要求提交即得到记录、拒绝或执行错误，同时工作台要能显示执行中状态；首轮没有队列或后台工人需求。被拒绝的方案：提交后立即返回 `running` 再轮询——会增加生命周期与崩溃恢复，却不增加首轮验收能力。重新评估：当出现长查询或需要查询历史页时再引入异步。

2. **审计库不保存结果网格。**  
   理由：背景要求 `GET` 读取状态和审计事实，未要求回放结果；持久化行集会把只读查询输出变成平台数据。被拒绝的方案：把 `result` JSON 写入 `query_runs`。后果：刷新工作台不会恢复上一张表，这与「最小工作台、非运营后台」一致。

3. **业务结果统一 HTTP 200，领域状态放在 `status`。**  
   理由：拒绝与失败必须留下可 `GET` 的同一表示；用 4xx/5xx 表达领域状态会让传输失败和策略拒绝混在一起。被拒绝的方案：`rejected` → 422、`failed` → 500。

4. **函数使用允许名单。**  
   理由：这是外部行为与安全边界。只读身份挡不住 `pg_read_file`、`dblink` 或耗尽 CPU 的函数；字符串黑名单会被注释和标识符绕过，也与背景的 AST 要求冲突。被拒绝的方案：只拦背景点名的语句类型，或维护拒绝名单。

5. **执行用户原文，而不是 SQLGlot 生成 SQL。**  
   理由：生成结果可能改变字面量或函数限定，造成工作台看到的语句与数据库执行不一致。只读身份与对象范围检查提供第二道防线。被拒绝的方案：始终 `sqlglot.transpile` 后再执行。

6. **超行数记为失败，不返回部分成功。**  
   理由：部分行被当成完整聚合或明细会改变业务判断。被拒绝的方案：静默 `LIMIT` 或带截断标记的 `succeeded`。

7. **无登录；能访问该 Compose 实例 Web 端口的人即可提交 SQL。**  
   理由：背景把用户注册和复杂 RBAC 列为非目标。访问边界是本地实例网络隔离，不是应用身份。被拒绝的方案：首轮加入共享口令或伪造用户表。重新评估：一旦离开本机演示或出现多租户，必须在应用层补身份。

8. **Web 反代 API，浏览器不直连 API 端口。**  
   理由：并行工作区只配置 Web 宿主端口即可，避免 CORS 和前端嵌入可变 API 源。API 端口仍可配置，供测试或调试直连。

9. **`analytics` 使用独立的分析所有者身份做迁移与 seed。**  
   理由：背景要求把固定数据写入 `analytics`，同时要求查询执行只用只读身份，且平台身份不得跨库。因此必须有第三个、永不执行用户 SQL 的写身份。被拒绝的方案：用平台身份连 `analytics`；或让分析只读身份在启动期临时可写。

## 失败 / 迁移

- 策略拒绝不接触分析表，只更新 `platform` 审计行。
- 执行失败仍保留原始 SQL、错误码、摘要和耗时；摘要去掉连接信息与内部堆栈。
- 启动迁移只向前升级。分析 seed 可重复执行并恢复到权威 CSV，会清掉对分析表的本地手工改动；这些改动不是产品状态。
- `platform.query_runs` 在重复启动时保留。seed 不清空审计表。
- 回滚应用版本不自动删除已写入的查询运行。若审计列变更，必须新增 Alembic 迁移，不得手工改库。
- 首轮不提供跨实例导出或结果缓存失效协议。

## 测试接缝

测试数量不是目标。三类测试都通过公共接口观察行为，不读取 SQLGlot 树或数据库系统表作为主断言。

### 机械

- 策略单元测试直接调用查询策略接缝，不启动浏览器、不连库。
- Vitest 覆盖工作台在 `running` / `succeeded` / `rejected` / `failed` 下的展示。
- 数据集被改动时，在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py`。

### 行为

1. **SQL 策略单元测试**必须覆盖：允许的 `SELECT` / `WITH` / 连接 / 子查询 / 聚合 / 窗口 / 集合运算；拒绝多语句、写操作、修改型 CTE、`SELECT INTO`、系统目录、未授权表、未允许函数；CTE 名称不误判为物理表；注释或字符串中的写关键字不导致误拒或误放。
2. **双数据库集成测试**必须证明：平台身份不能连接或读取 `analytics` 业务表；分析所有者身份不能连接 `platform`；分析只读身份不能连接 `platform`，也不能对分析表 `INSERT`/`UPDATE`/`DELETE`；一次成功查询会在 `platform` 留下审计行，且执行发生在分析只读身份下；`/ready` 在迁移与 seed 完成前不成功。
3. **浏览器主流程**必须覆盖：输入允许的 SQL → 看到执行中 → 看到结果表；输入被拒 SQL → 看到拒绝码与说明；不要求截图对比或视觉回归。

### 共识

- 契约字段、已实现销售额口径、最小端点路径若要调整，先改背景资料或契约，再改设计与测试；测试通过不代表可以改口径。

## 延后决策

- 查询运行卡在 `running` 时的回收或标记失败。
- 异步执行、查询列表、结果回放。
- 应用层认证与按用户划分的对象范围。
- 函数允许名单的扩充；未出现真实分析场景前不扩大。
- 组件的精确发行版本号：按技术约束在应用建立后写入依赖清单，不在本文冻结。
