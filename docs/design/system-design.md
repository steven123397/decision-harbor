# 系统设计（首轮：受治理 SQL 查询链路）

本文档是首轮实现的正式设计，覆盖范围与非目标、模块与数据流、查询运行状态与审计事实、双数据库与只读身份边界、SQLGlot AST 策略与资源限制、最小 API 与错误语义、查询工作台、迁移与幂等 seed、Compose 并行隔离，以及单元/集成/浏览器测试接缝。术语见 [CONTEXT.md](CONTEXT.md)；事实来源为 `docs/background/` 与 `datasets/sales-analytics-v1/contract.json`，二者已确认，本设计不改写。

## 1. 范围与背景

**问题**：企业内部业务人员需要在最小查询工作台中提交显式 SQL，系统在受控规则内完成校验、只读执行、结果展示与查询审计。

**输入事实**：

- `docs/background/product-requirements.md` 确定目标、非目标、固定数据口径、SQL 治理规则、最小 API 与本地运行要求。
- `docs/background/technical-constraints.md` 固定技术栈（Node 24 + React 19 + TS + Vite；Python 3.13 + FastAPI + Pydantic；PostgreSQL 18 + SQLAlchemy 2.0 + Alembic + psycopg 3；SQLGlot；Docker Compose；pytest/Vitest/Playwright）与并行隔离要求。
- `datasets/sales-analytics-v1/contract.json` 固定五张 `analytics` 表的 schema、字段、计数与业务口径。

**首轮交付**：一条显式 SQL 查询链路，打通前端工作台、API、数据库与本地基础设施；后续阶段在此基础上扩展。

## 2. 目标与非目标

**目标**（与产品需求一致）：

1. 在只安装 Git、Docker、Docker Compose 的干净 WSL 环境中，可启动、迁移、填充数据并运行测试。
2. 受治理 SQL 查询执行模块：基于 SQLGlot AST 与对象访问范围校验输入，使用只读身份执行。
3. 最小查询工作台：输入 SQL、提交、查看执行中状态、展示结果表格或拒绝原因。
4. 审计记录：原始 SQL、策略判定或拒绝原因、执行状态、返回行数、耗时、错误摘要与创建时间。

**非目标**：LLM、NL2SQL、RAG、MCP、A2A；用户注册、复杂 RBAC、运营后台、图表编辑器、前端视觉专项；跨库访问平台状态；用平台可写身份执行用户 SQL。以上不做，也不为本设计预留接口，仅列入第 9 节延后决策。

## 3. 领域术语与场景

术语见 [CONTEXT.md](CONTEXT.md)。核心场景与边界案例：

- **允许查询**：提交一条只读 `SELECT` / `WITH ... SELECT` / 集合运算 → 策略允许 → `202 running` → 后台执行 → 轮询后 `succeeded` + 结果表。
- **策略拒绝**：提交多语句、写语句、数据修改型 CTE、`SELECT INTO` 或未授权对象 → `422 rejected` + 稳定错误码 + 记录标识。
- **执行失败**：语句超时、结果行数超上限、数据库错误 → 终态 `failed` + 稳定错误码。
- **空输入**：空串或仅空白/注释 → `rejected`（`POLICY_EMPTY_STATEMENT`）。
- **字符串/注释含关键字**：如字符串 `'DROP TABLE'` 或注释中含 `INSERT`，不得误判——判定必须基于 AST 而非文本黑名单。
- **未授权对象**：引用 `platform` 的表、`pg_catalog`、`information_schema`、其他 schema 或跨库限定名 → `rejected`（`POLICY_UNAUTHORIZED_OBJECT`）。
- **重启遗留**：API 重启时存在 `running` 记录 → 启动收敛为 `failed`（`EXEC_INTERRUPTED`）。

## 4. 状态与不变量

**查询运行状态机**（单向，无回退）：

    提交 ──策略──▶ rejected（终态，策略拒绝）
       │
       └──策略允许──▶ running ──执行成功──▶ succeeded（终态）
                          │
                          └──执行失败──▶ failed（终态）

**全局不变量**：

- I1：每次 SQL 提交恰产生一条 `query_run` 记录，含拒绝与失败（无效 HTTP 请求体除外）。
- I2：唯一非终态是 `running`；终态为 `rejected`/`succeeded`/`failed`；转移单向，终态不回到 `running`。
- I3：用户 SQL 只经 `analytics_reader` 在 `analytics` 上执行；绝不使用 `dh_admin` 或 `platform_writer` 执行用户 SQL。
- I4：策略允许的对象集合恒等于 `contract.json` 声明的五张表；系统目录、其他 schema 或数据库一律拒绝。
- I5：执行器对每次执行都施加语句超时与结果行数上限，且两值可配置。
- I6：`analytics` 数据恒等于权威 CSV（幂等 seed，可被 `validate.py` 校验哈希）。
- I7：API 重启后无遗留 `running`（启动收敛为 `failed`）。
- I8：审计记录 `id` 不可变；原始 SQL 一经写入不重写，仅状态字段随生命周期更新。

## 5. 方案与模块边界

### 5.1 架构与服务

    浏览器 ──HTTP(经 CORS)──▶ Web(工作台) ──HTTP──▶ API(FastAPI)
                                                      │  platform_writer：审计读写(platform)
                                                      │  analytics_reader：执行(analytics)
                                                      ▼
                                           PostgreSQL(单容器：platform + analytics)

三个 Compose 服务：`db`（PostgreSQL 单容器承载两个逻辑库）、`api`（FastAPI）、`web`（React 静态构建）。迁移与 seed 由 `api` 启动阶段以 `dh_admin` 身份执行；运行时 `api` 持两套连接：`platform_writer` 写审计、`analytics_reader` 执行用户 SQL。

### 5.2 数据流

    1. 用户在工作台输入 SQL → POST /api/v1/query-runs
    2. API 校验请求体 → 治理模块 policy.check(sql)
    3. 判定 denied → 审计 store.create(status=rejected) → 返回 422(rejected+error+id)
    4. 判定 allowed → store.create(status=running) → 调度后台执行 → 返回 202(running+id)
    5. 执行器经 analytics_reader 在 analytics 执行：
       - 成功 → store.update(status=succeeded, row_count, duration_ms) + 结果写入内存缓存
       - 失败 → store.update(status=failed, error_code, error_summary, duration_ms)
    6. 工作台轮询 GET /api/v1/query-runs/{id}：running 时仅返回状态；succeeded 且缓存命中时附结果行；rejected/failed 时附错误。

### 5.3 模块与深模块自检

| 模块 | 职责 | 最小接口 | 隐藏在接口后的复杂度 | 维护的不变量 | 失败表达 | 测试接缝 |
| --- | --- | --- | --- | --- | --- | --- |
| 治理策略 `query_policy` | 解析并校验 SQL | `check(sql) -> PolicyDecision` | SQLGlot 解析、AST 遍历、对象范围比对、拒绝原因归类 | 只允许单条只读 SELECT，对象限五表 | 返回 denied 决策（对用户输入不抛异常） | 单元测试直接调用 |
| 对象目录 `analytics_schema` | 提供授权对象与 DDL | `tables() / ddl()` | contract.json 解析、类型映射、FK 排序 | 与 contract.json 恒等 | 启动期契约缺失/损坏即失败 | 单元测试（contract 加载） |
| 执行器 `query_executor` | 只读执行并受限返回 | `execute(sql, limits) -> ExecResult` | 只读事务、语句超时、行上限+溢出探测、列元数据 | 永不写；每次执行都受限 | 抛 `ExecutionError(code, summary)` | 集成测试 |
| 审计存储 `query_run_store` | 持久化查询运行 | `create / update_status / get` | 平台连接、状态合法性校验、字段映射 | id 不可变；状态转移合法 | 抛存储错误（映射为 EXEC_INTERNAL） | 集成测试 |
| 编排服务 `query_service` | 提交/查询/状态机/收敛 | `submit(sql) / get(id)` | 后台执行调度、状态转移、结果缓存、启动收敛 | I1/I2/I7 | 把 ExecutionError 落为 failed | 集成测试 |
| API 层 `api` | HTTP 端点与封套 | 见 5.4 | Pydantic 校验、封套映射、CORS | 响应形状稳定 | 4xx/5xx + 错误码 | 集成/浏览器测试 |
| 迁移与 seed `seed` | 建库建角色、迁移、幂等填充 | `migrate() / seed_analytics()` | Alembic(platform)、contract 驱动 DDL+CSV 装载、幂等 | I6 | 启动失败即阻断就绪 | 集成测试（幂等/权限） |
| 工作台 `web` | 输入/提交/展示 | 单一页面 | 轮询、状态渲染、结果表 | 无本地 SQL 状态持久化 | 界面错误提示 | Playwright |

**深模块自检要点**：调用方只需知道 `query_service` 的 `submit/get` 与 API 封套；SQLGlot 解析细节、只读事务、行溢出探测、缓存淘汰全部隐藏在各模块接口之后；调用方与测试通过同一公共接缝（`query_policy.check`、`query_service.submit`、HTTP 端点）观察行为，不依赖内部结构。

### 5.4 最小 API 契约

| 方法 | 路径 | 用途 | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| GET | `/health` | 进程存活 | 200 `{"status":"ok"}` | — |
| GET | `/ready` | 迁移与 seed 就绪 | 200 | 503 `{"status":"not_ready"}` |
| POST | `/api/v1/query-runs` | 提交 `{"sql":"..."}` | 202（running） | 422（rejected）/ 400（INVALID_REQUEST） |
| GET | `/api/v1/query-runs/{id}` | 读状态与审计事实 | 200 | 404（NOT_FOUND） |

查询运行响应为单一封套（`result` 仅在 `succeeded` 且结果缓存命中时出现）：

    {
      "id": "<uuid>",
      "sql": "<原始 SQL>",
      "status": "running|succeeded|failed|rejected",
      "created_at": "<ISO8601>",
      "started_at": "<ISO8601>|null",
      "finished_at": "<ISO8601>|null",
      "row_count": 123|null,
      "duration_ms": 42|null,
      "error": { "code": "...", "message": "..." } | null,
      "result": { "columns": [{"name":"...","type":"..."}], "rows": [[...], ...] } | null
    }

## 6. 关键决策

### D1 异步执行 + 内存结果缓存

- **结论**：POST 同步完成策略判定；允许后创建 `running` 记录并后台执行，返回 `202`；工作台轮询 GET。执行结果放入有界内存缓存，GET 在缓存命中时返回结果行。
- **理由**：需求要求“查看执行中状态”，使 `running` 成为可观测状态；解耦长查询与 HTTP 超时；审计只持久化事实，结果行属临时载荷。
- **被拒替代方案**：同步阻塞 POST（“执行中”退化为客户端转圈，长查询易超时）；结果行持久化入库（可靠但首轮过重）。
- **后果**：进程重启后结果缓存丢失（审计元数据仍在）；缓存有 LRU 上限与 TTL。
- **重估条件**：出现多实例或要求重启后仍可取结果时，引入持久化结果存储。

### D2 结果行数超上限即失败

- **结论**：执行器设置行数上限（默认 10000，可配置），取 `上限+1` 行探测溢出；溢出即 `failed` + `EXEC_ROW_LIMIT_EXCEEDED`，不返回部分结果。
- **理由**：部分结果可能误导分析；失败语义稳定、可测试，且满足“结果行数上限”的硬约束。
- **被拒替代方案**：返回前 N 行并带 `truncated` 标志。
- **后果**：用户需改写 SQL 缩小结果。
- **重估条件**：业务要求“先看前 N 行”时，改为截断+标志。

### D3 对象（表）级 allowlist，源自 contract.json

- **结论**：策略只做表级对象访问范围校验；授权集合启动时从 `contract.json` 生成；列级校验交由 PostgreSQL 处理。
- **理由**：需求写“对象访问范围”“未授权对象”，为表级；从契约生成避免策略与数据集漂移；列错误由数据库在执行期报错（归为 `failed`）。
- **被拒替代方案**：列级 allowlist（更细但首轮无需求，且契约无列级敏感标记）。
- **后果**：引用授权表外的列在执行期报错，而非策略拒绝。
- **重估条件**：出现列级敏感需求时收紧。

### D4 三种数据库身份分离

- **结论**：`dh_admin`（仅迁移/seed）、`platform_writer`（审计读写）、`analytics_reader`（用户 SQL 只读）。运行时用户 SQL 仅经 `analytics_reader`。
- **理由**：双边界要求（策略 + DB 只读）；审计与数据执行分离，防止用户 SQL 影响平台状态。
- **被拒替代方案**：复用单一超级用户（违反只读边界，直接淘汰）。
- **后果**：引导期需建库建角色；凭据管理更细。
- **重估条件**：无。

### D5 analytics 由 seed 从 contract.json 生成，platform 用 Alembic

- **结论**：`platform` 结构走 Alembic 迁移；`analytics` 表与数据由幂等 seed 从 `contract.json` 生成。
- **理由**：`analytics` 是固定外部数据集，契约是唯一事实源，手写迁移易漂移；`platform` 是应用自设计状态，需版本化迁移。
- **被拒替代方案**：`analytics` 也手写 Alembic 迁移（与契约漂移风险）。
- **后果**：`analytics` 结构变更即契约变更，需重跑 seed。
- **重估条件**：无。

### D6 幂等 seed 采用截断重载（analytics）

- **结论**：`analytics` seed 在一个事务内按 FK 顺序截断五表并从权威 CSV 重载；重复执行结果一致，不产生重复。
- **理由**：`analytics` 只承载权威固定数据、无用户数据，截断重载天然幂等且可对齐 `validate.py` 哈希校验。
- **被拒替代方案**：UPSERT（复杂且无必要，数据不累积）。
- **后果**：重载短暂持有表锁（本地启动期 seed，用户查询尚未进入）；`platform` 永不截断。
- **重估条件**：无。

### D7 拒绝返回 422，执行失败返回 200

- **结论**：策略拒绝 → `422`，body 为 `status=rejected` 的查询运行 + 错误码；执行失败 → `200`，body 为 `status=failed` 的查询运行 + 错误码。
- **理由**：区分“请求不被允许”（4xx）与“资源已建但处理失败”（2xx+状态字段）；同时满足“统一 JSON + 稳定错误码 + 记录标识”。
- **被拒替代方案**：全部 200 + 状态字段（丢失 HTTP 语义）；拒绝也 200（与 4xx 语义冲突）。
- **后果**：客户端需同时处理 4xx 与 2xx 两种失败形态（封套一致，仅状态码不同）。
- **重估条件**：下游只消费状态字段时统一为 200。

### D8 稳定错误码分类

- **结论**：错误码分 `POLICY_*` / `EXEC_*` / 请求类，码值稳定、`message` 可读（清单见第 7 节）。
- **理由**：需求要求“稳定错误码 + 可读说明”；分类使前端可稳定分支。
- **被拒替代方案**：仅 message 无码（前端无法稳定处理）。
- **后果**：码表向后兼容，不删除既有码。
- **重估条件**：新能力补充新码，不修改既有码语义。

### D9 Web↔API 经 CORS 直连

- **结论**：浏览器直接调用 API 宿主端口，API 开启对 Web 源（可配置）的 CORS。
- **理由**：两服务独立，无需反向代理；并行实例各用不同端口，源可配置。
- **被拒替代方案**：Vite 代理（仅开发）；API 反代静态资源（服务耦合）。
- **后果**：浏览器需允许跨源；源需配置。
- **重估条件**：要求同源或生产部署时引入反向代理。

### D10 Compose 并行隔离

- **结论**：Compose 项目名可配置（`COMPOSE_PROJECT_NAME`）；Web/API 宿主端口可配置（`WEB_PORT` / `API_PORT`）；DB 不映射宿主端口；不设 `container_name`、固定网络名、全局卷名或共享绑定目录；PG 数据用项目作用域命名卷。
- **理由**：需求“多个本地工作区并行”；Compose 项目名天然隔离容器/网络/卷命名空间。
- **被拒替代方案**：固定端口（冲突）。
- **后果**：每实例用独立 `.env` 覆盖端口。
- **重估条件**：无。

### D11 启动收敛遗留 running

- **结论**：API 启动时把遗留 `running` 记录收敛为 `failed` + `EXEC_INTERRUPTED`。
- **理由**：保证 I7（无永久 running），进程重启不产生悬空状态。
- **被拒替代方案**：重启后重试（复杂且可能重复执行）。
- **后果**：重启丢失在途查询（本地工具可接受）。
- **重估条件**：要求可靠重放时引入持久化队列。

## 7. 失败、安全与迁移

### 7.1 错误码

策略拒绝（`rejected`）：

- `POLICY_PARSE_ERROR`：SQLGlot 无法解析。
- `POLICY_EMPTY_STATEMENT`：空或仅空白/注释。
- `POLICY_MULTIPLE_STATEMENTS`：多于一条语句。
- `POLICY_NON_SELECT`：顶层不是 SELECT / 集合运算。
- `POLICY_FORBIDDEN_STATEMENT`：含 INSERT/UPDATE/DELETE/MERGE/CREATE/ALTER/DROP/TRUNCATE/COPY/CALL/DO。
- `POLICY_DATA_MODIFYING_CTE`：数据修改型 CTE。
- `POLICY_SELECT_INTO`：`SELECT INTO`。
- `POLICY_UNAUTHORIZED_OBJECT`：引用非授权对象、系统目录或跨 schema/库。
- `POLICY_UNSUPPORTED_SYNTAX`：其他未支持语法。

执行失败（`failed`）：

- `EXEC_DB_ERROR`：数据库执行错误（含列不存在、类型错误等）。
- `EXEC_TIMEOUT`：超过语句超时。
- `EXEC_ROW_LIMIT_EXCEEDED`：结果行数超上限。
- `EXEC_INTERRUPTED`：进程重启导致在途运行中断。
- `EXEC_INTERNAL`：未预期内部错误。

请求错误：`INVALID_REQUEST`（缺 sql / 非字符串 / 超长）、`NOT_FOUND`。

### 7.2 HTTP 语义

- POST 合法 → `202` + `status=running`；策略拒绝 → `422` + `status=rejected`；请求体非法 → `400` + `INVALID_REQUEST`（不创建记录）。
- GET 存在 → `200`；不存在 → `404` + `NOT_FOUND`。
- `/health` 恒 `200`；`/ready` 就绪 `200` / 未就绪 `503`。

### 7.3 权限与安全边界

- `analytics_reader`：`NOSUPERUSER / NOCREATEDB / NOCREATEROLE`，仅 `CONNECT` on `analytics`、`USAGE` on `analytics` schema、`SELECT` on 五张表；`default_transaction_read_only=on`。无 `COPY`、无 `dblink`/FDW、无危险函数（如 `pg_read_file` 需超级用户或显式授权，未授予）。
- `platform_writer`：仅对 `platform` 审计表有读写，不授予 `analytics` 任何权限。
- `dh_admin`：仅引导期建库建角色、执行迁移与 seed；运行时 API 不持其凭据。
- 策略（AST+对象范围）是第一道边界，数据库只读身份是第二道；二者独立失效都不改变整体只读结果。

### 7.4 审计 schema（platform，Alembic 管理）

    query_runs (
      id                uuid        PRIMARY KEY,
      created_at        timestamptz NOT NULL,
      sql_text          text        NOT NULL,
      policy_verdict    text        NOT NULL CHECK (policy_verdict IN ('allowed','denied')),
      status            text        NOT NULL CHECK (status IN ('rejected','running','succeeded','failed')),
      rejection_code    text        NULL,
      rejection_message text        NULL,
      error_code        text        NULL,
      error_summary     text        NULL,
      row_count         bigint      NULL,
      duration_ms       bigint      NULL,
      started_at        timestamptz NULL,
      finished_at       timestamptz NULL
    )

    dataset_seed (
      dataset         text PRIMARY KEY,
      version         text NOT NULL,
      contract_sha256 text NOT NULL,
      seeded_at       timestamptz NOT NULL
    )

`dataset_seed` 记录 seed 状态，支撑 `/ready` 判定与幂等跳过（同版本已 seed 则跳过）。

### 7.5 配置面（.env.example）

- 隔离：`COMPOSE_PROJECT_NAME`、`WEB_PORT`、`API_PORT`。
- 数据库：`POSTGRES_USER` / `POSTGRES_PASSWORD`、`PLATFORM_DB`、`ANALYTICS_DB`、三个身份名与口令。
- 资源限制：`QUERY_TIMEOUT_MS`（默认 30000）、`QUERY_ROW_LIMIT`（默认 10000）、`MAX_SQL_BYTES`（默认 65536）。
- Web：`CORS_ORIGIN`（默认 Web 源）。

### 7.6 迁移、seed 与回滚

- 引导顺序：建库 `platform`/`analytics` → 建三角色并授权 → Alembic 迁移 `platform`（query_runs、dataset_seed）→ seed `analytics`（按 contract 建表 + 按 FK 顺序截断重载 CSV）→ 写 `dataset_seed`。
- 幂等：迁移只增不删（Alembic 版本）；seed 截断重载 + 版本标记，重复执行结果一致；同版本 seed 跳过。
- 回滚：`analytics` 可随时重跑 seed 恢复权威数据；`platform` 由 Alembic downgrade 处理；因 `analytics` 无用户数据，不设数据迁移回滚。
- 失败语义：引导任一环节失败 → `/ready` 返回 503，阻塞依赖就绪的测试与工作台。

## 8. 测试接缝与验证原则

### 8.1 SQL 策略单元测试（pytest，纯函数，无 DB）

接缝：`query_policy.check(sql)`。覆盖：

- 允许：单条 SELECT、WITH...SELECT、连接、子查询、聚合、窗口函数、UNION/INTERSECT/EXCEPT。
- 拒绝：多语句、各写语句、数据修改型 CTE、SELECT INTO、未授权对象、系统目录、跨 schema/库。
- 边界：解析错误、空输入、字符串/注释含关键字不误判、顶层非 SELECT。

### 8.2 双数据库集成测试（pytest + 真实 PostgreSQL）

接缝：`query_service.submit/get`、`seed`、真实角色。覆盖：

- seed 幂等：连跑两次，五表计数与哈希不变，无重复。
- 职责分离：`platform_writer` 可写审计；`analytics_reader` 可 SELECT 但 INSERT 被数据库拒绝（权限错误）。
- 执行器：允许查询返回正确行；超时、行上限溢出分别归为对应 `EXEC_*` 码。
- 审计：提交 → running → succeeded/failed 全程落库，字段与状态一致；拒绝亦落库。
- 启动收敛：预置 running 记录，重跑启动逻辑后变为 failed/EXEC_INTERRUPTED。

### 8.3 浏览器主流程（Playwright，针对已启动的 Compose 栈）

接缝：HTTP API + 工作台 UI。覆盖：输入 SQL → 提交 → 出现执行中状态 → 结果表格；输入被拒 SQL → 显示拒绝原因。

### 8.4 统一命令与证据类型

- 统一测试命令编排 `pytest`（单元+集成）、`vitest`（前端最小单测）、`playwright test`；统一运行命令构建并启动 Compose、迁移、seed 后等待 `/ready`。
- 证据分层：单元与集成提供**机械证据**（策略判定、权限拒绝、seed 幂等可脚本断言）；浏览器主流程与 API 契约提供**行为证据**（用户可见行为符合需求）；D1–D11 属**设计决策**（已记录理由与替代方案），不把测试通过误当作需求共识，也未扩大首轮范围。

## 9. 延后决策

下列项明确不属于首轮，触发条件满足时再评估：

- 认证/授权与多用户（当前单用户本地工具）。
- 结果持久化、分页与导出（当前内存缓存 + 单次返回）。
- 查询取消 API（当前只支持等待终态）。
- 持久化队列 / 多实例执行（当前单 API 实例内存后台执行）。
- 除 `analytics` 外的数据源接入。
- 列级对象范围校验。
- 结果截断+标志模式（替代 D2）。
- 监控指标与结构化日志（当前仅健康/就绪）。
