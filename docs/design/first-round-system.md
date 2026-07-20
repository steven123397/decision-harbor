# 首轮系统设计

## 1. 范围与背景

### 1.1 问题

企业内部业务人员需要在受控环境中提交显式 SQL，获得可审计的只读分析结果。首轮建立跨 Web、API、PostgreSQL 与本地 Compose 的可运行基座，而不是完整分析平台。

### 1.2 输入事实与约束来源

| 来源 | 用途 |
| --- | --- |
| `docs/background/product-requirements.md` | 目标/非目标、双库边界、SQL 治理、最小 API、本地运行与验证 |
| `docs/background/technical-constraints.md` | 技术栈、架构边界、并行隔离、质量基线 |
| `datasets/sales-analytics-v1/contract.json` 与公开 CSV | 分析表结构、行数、关联、业务口径与权威 fixture |
| 根 `AGENTS.md` | 协作与安全边界（不改写背景） |

本设计细化实现结构与关键取舍；**不改写**背景中的业务口径、五表字段语义、允许/拒绝 SQL 类别、端点清单与技术栈选型。

### 1.3 覆盖与不覆盖

**覆盖：**

- 首轮模块划分与端到端数据流
- 查询运行状态机与平台审计模型
- `platform` / `analytics` 逻辑库与数据库身份
- SQLGlot AST 策略、对象白名单与资源限制
- 最小 HTTP API 与错误语义
- 最小查询工作台行为
- 迁移、幂等 seed 与 Compose 并行隔离
- 单元 / 集成 / 浏览器测试接缝

**不覆盖：** 自然语言转 SQL、LLM/RAG/MCP/A2A、用户注册与复杂 RBAC、运营后台、图表编辑器、多租户云部署、结果集长期存储与导出中心。

## 2. 目标与非目标

### 2.1 目标

1. 干净 WSL（仅需 Git、Docker、Docker Compose）一条命令可构建并启动 Web、API、PostgreSQL，完成迁移与 seed，并通过健康/就绪检查。
2. API 内基于 SQLGlot AST 与对象访问范围治理用户 SQL；通过后仅以分析只读身份在 `analytics` 执行。
3. 最小查询工作台支持输入、提交、执行中态、结果表或拒绝/失败信息。
4. 每次提交产生可追踪的查询运行审计（原始 SQL、策略/错误、状态、行数、耗时、时间戳）。
5. 统一测试入口覆盖策略单元测试、双库集成测试、工作台浏览器主流程。
6. 多工作区可并行：可配置 Compose 项目名与 Web/API 宿主端口；无固定全局容器/网络/卷名。

### 2.2 非目标

与背景一致，并补充实现层明确不做项：

- 不引入异步任务队列、独立 worker 进程或查询排队调度。
- 不持久化完整结果网格；不提供历史结果回放表格（历史以审计元数据为准）。
- 不实现终端用户登录、API Key、行级安全或多角色业务权限模型。
- 不在策略层做字符串黑名单式“安全”；不以改写用户 SQL（如静默注入 `LIMIT`）代替显式资源错误（见第 6 节决策）。
- 不把 `platform` 暴露给用户 SQL，不在用户查询路径使用平台可写身份或分析迁移身份。

## 3. 术语与场景

术语见 [CONTEXT.md](./CONTEXT.md)。

### 3.1 关键场景

| ID | 场景 | 期望 |
| --- | --- | --- |
| S1 | 合法只读聚合（如按区域统计已确认订单销售额） | 策略通过 → 执行成功 → 工作台展示列与行；审计 `succeeded` |
| S2 | 多语句或写操作 | 策略拒绝 → 不触达分析库执行连接的查询；审计 `rejected` |
| S3 | 引用系统目录或未授权表 | 策略拒绝；审计 `rejected` |
| S4 | 合法但超行数上限或超时 | 策略通过 → 执行失败；审计 `failed`，稳定错误码 |
| S5 | 打开工作台提交后等待 | UI 在请求未完成前显示执行中；完成后切换结果或错误 |
| S6 | 按 ID 回读 | `GET` 返回该次审计事实；不含完整结果网格（若未曾成功返回，则无行数据） |
| S7 | 重复执行迁移与 seed | 不破坏性重复；分析数据与契约行数一致；审计表结构保持可升级 |

### 3.2 边界案例

- 空 SQL、仅注释、或无法解析：视为策略/输入拒绝，写入 `rejected`（或请求体校验失败，见 API 节），不执行。
- `WITH` 数据修改型 CTE、`SELECT INTO`、`COPY`、`CALL`、`DO`：拒绝。
- `UNION` / `INTERSECT` / `EXCEPT` 与窗口函数：允许，但仍受对象白名单与资源限制。
- 金额与毛利：由用户 SQL 按契约公式自行计算；系统不预置物化视图作为首轮必交付项。

## 4. 状态与不变量

### 4.1 查询运行状态

首轮在 **API 请求线程内同步** 完成策略与执行（无独立队列）。状态集合：

| 状态 | 含义 | 是否终态 |
| --- | --- | --- |
| `running` | 记录已创建，正在策略检查或执行 | 否 |
| `succeeded` | 策略允许且执行成功 | 是 |
| `rejected` | 策略或可判定的输入不合法；未执行用户 SQL | 是 |
| `failed` | 策略允许后执行失败（超时、行数、数据库错误等） | 是 |

**状态迁移：**

```text
(创建记录) → running → succeeded
                   → rejected
                   → failed
```

禁止从终态回到 `running`。禁止 `rejected` 与 `failed` 互换。进程崩溃可能导致残留 `running`；首轮不实现自动回收，运维上可视为异常审计行（延后决策可加超时修复任务）。

### 4.2 审计事实（`platform`）

表名建议：`query_runs`。字段为产品内生设计，不与分析契约混用：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID，主键，对外路径参数 |
| `sql_text` | 用户提交的原始 SQL 文本 |
| `status` | `running` / `succeeded` / `rejected` / `failed` |
| `error_code` | 稳定机器码；成功时为空 |
| `error_message` | 可读摘要；成功时为空 |
| `row_count` | 成功时为返回行数；否则为空 |
| `duration_ms` | 从进入处理到终态的耗时毫秒；创建瞬间可为空，终态必填 |
| `created_at` | 创建时间（timestamptz） |
| `finished_at` | 进入终态时间；`running` 时为空 |

**不存：** 结果列定义、结果行、执行计划、用户身份（首轮无用户模型）。

成功时的列定义与行数据仅出现在 `POST /api/v1/query-runs` 的响应中，供工作台当次展示。

### 4.3 系统不变量

1. 用户 SQL **只** 在 `analytics` 上、**只** 通过分析只读身份执行。
2. 查询运行审计 **只** 写在 `platform`，且只通过平台可写身份访问。
3. 策略未 `allow` 前，不得对分析库发起用户 SQL 执行。
4. 策略基于 AST 与对象范围，不得以“禁止关键字列表”作为唯一实现。
5. 分析业务表结构与公开 fixture 的字段、类型语义、业务口径与 `contract.json` 一致；seed 以已提交 CSV 为权威数据。
6. 金额使用定点数语义（数据库 `numeric`）；不在订单表冗余存储订单总额。
7. `/ready` 为真仅当：API 能连接所需库，且迁移已应用，且分析 seed 已达预期就绪条件。
8. Compose 实例之间不共享容器名、网络名、数据卷名或必须独占的宿主绑定目录。

## 5. 方案与模块边界

### 5.1 逻辑模块

```text
┌─────────────┐     HTTP      ┌──────────────────────────────────────┐
│  Web 工作台  │ ───────────► │  API (FastAPI)                       │
│  React/Vite │ ◄─────────── │  ├─ health/ready                      │
└─────────────┘               │  ├─ QueryRunService                  │
                              │  ├─ SqlPolicy (SQLGlot)               │
                              │  ├─ QueryExecutor (analytics RO)      │
                              │  └─ QueryRunRepository (platform RW) │
                              └───────────┬──────────────┬───────────┘
                                          │              │
                                          ▼              ▼
                                   platform DB     analytics DB
                                   (审计)           (五张业务表)
                                          ▲              ▲
                                          │              │
                              迁移(Alembic)         迁移 + seed
                              platform 角色          迁移角色 / RO 角色
```

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| Web 工作台 | SQL 输入、提交、执行中 UI、结果表/错误展示；调用最小 API | 策略实现、直连数据库、运营后台 |
| API 路由 | HTTP 契约、请求校验、状态码映射 | 业务规则细节（委托服务） |
| QueryRunService | 编排：建单 → 策略 → 执行 → 更新审计 → 组装响应 | SQL 解析细节、SQLAlchemy 会话底层 |
| SqlPolicy | 解析、单语句、语句形态、对象白名单、策略错误码 | 执行、超时、行数截断 |
| QueryExecutor | 使用只读身份执行、语句超时、行数上限、映射执行错误 | 策略判定、写 platform |
| QueryRunRepository | `query_runs` 的插入与更新、按 ID 读取 | 分析数据访问 |
| 迁移/seed 工具 | 建库角色与 schema、加载 CSV、幂等 | 运行时查询路径 |
| Compose 运行时 | 三服务协同、环境注入、并行隔离 | 业务逻辑 |

### 5.2 数据流（S1 成功路径）

1. 工作台 `POST /api/v1/query-runs`，body：`{ "sql": "..." }`。
2. Service 在 `platform` 插入 `running` 记录。
3. SqlPolicy 解析 AST；通过则继续，否则更新为 `rejected` 并返回。
4. QueryExecutor 以分析只读身份在 `analytics` 执行；施加超时与行数上限。
5. 成功：更新 `succeeded`、`row_count`、`duration_ms`、`finished_at`；响应带 `columns` 与 `rows`。
6. 失败：更新 `failed` 与错误码/摘要；响应不带结果行。

### 5.3 仓库布局建议（实现时）

不强制目录名与此完全一致，但职责应可映射：

- `apps/web` — React 19 + Vite 工作台
- `apps/api` — FastAPI、策略、执行器、platform 仓储
- `deploy/compose` 或仓库根 `docker-compose.yml` — 本地编排
- `datasets/sales-analytics-v1` — 保持现有契约与 fixture，seed 只读引用

## 6. 关键决策

| 决策 | 选项 | 选择 | 理由 | 被拒方案与后果 |
| --- | --- | --- | --- | --- |
| 执行模型 | 同步请求内完成 / 异步队列 + worker | **同步** | 背景未要求队列；首轮链路短、易测、状态简单 | 异步增加组件与一致性复杂度；若未来长查询成常态可再评估 |
| 结果持久化 | 全量存 DB / 只存元数据 | **只存审计元数据** | 背景审计字段未要求存网格；降低 platform 体积与敏感结果留存面 | 全量存储需额外保留策略与磁盘设计；GET 无法回放表格 |
| 行数限制实现 | 静默注入 `LIMIT` / 超限 `failed` | **超限记 `failed`，错误码 `EXECUTION_ROW_LIMIT`** | 静默改写改变用户语义，不利于治理透明 | 注入 `LIMIT` 可能让用户误以为全量结果 |
| 超时实现 | 仅应用层 cancel / DB `statement_timeout` + 应用感知 | **连接级/事务级 `statement_timeout`，映射为 `EXECUTION_TIMEOUT`** | 数据库侧强制比仅依赖客户端更可靠 | 仅应用 cancel 在驱动/阻塞场景可能泄漏负载 |
| 策略与权限 | 只做 AST / 只做 DB 权限 / 双边界 | **双边界** | 背景明确要求；防御策略实现缺陷与权限配置漂移 | 单边界任一漏洞即越权 |
| 分析 schema 名 | 使用契约 `"schema": "analytics"` / 仅 `public` | **表建于 `analytics` 库的 `public` schema，对象引用按表名白名单；不要求用户 SQL 写库名** | PostgreSQL 逻辑库已隔离；跨库名出现在 SQL 中一律拒绝 | 强制三层命名增加工作台负担，且与“单库连接”模型不一致 |
| 首轮鉴权 | 无鉴权本地开放 / 基本 Auth | **无终端用户鉴权** | 背景排除注册与复杂 RBAC；对象为本地受控环境 | 若暴露到不可信网络必须停止并补鉴权（延后） |
| 默认资源上限 | 固定硬编码 / 环境可配默认值 | **环境可配，提供安全默认**（见下） | 便于并行实例与测试收紧限制 | 硬编码妨碍集成测试与演示调节 |

**默认资源上限（可经环境变量覆盖）：**

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| 语句超时 | `5000` ms | 映射 PostgreSQL `statement_timeout` |
| 结果行数上限 | `1000` | 拉取超过上限则失败，不返回部分成功 |

## 7. 双数据库与身份边界

### 7.1 拓扑

- 单一 PostgreSQL 18 容器内两个逻辑数据库：`platform`、`analytics`。
- API 持有至少两种运行时连接配置：平台可写 DSN、分析只读 DSN。
- 迁移/seed 使用独立高权限凭据（可与启动入口脚本相同），**不得**注入到查询执行器配置。

### 7.2 角色与权限（逻辑）

| 角色 | 数据库 | 权限要点 |
| --- | --- | --- |
| `platform_app` | `platform` | 对 `query_runs`（及 alembic 版本表）DML；连接 `platform` |
| `analytics_migrator` | `analytics` | 迁移 DDL + seed 写入；仅运维路径 |
| `analytics_readonly` | `analytics` | 仅对五张授权业务表 `SELECT`；无 CREATE/UPDATE/DELETE；无平台库连接 |

额外加固（实现应尽量做到）：

- 撤销只读角色对非业务对象的访问；默认撤销 public 上多余权限。
- 用户 SQL 会话禁止切换角色、禁止写临时可写逃逸（只读事务 / `default_transaction_read_only` 若可行则启用）。
- API 进程内分连接池：platform 池与 analytics 只读池物理分离，代码路径不可混用 session。

### 7.3 禁止行为

- 使用 `platform_app` 执行用户 SQL。
- 使用 `analytics_migrator` 服务在线查询 API。
- 在用户 SQL 中引用 `platform` 对象、`pg_catalog` / `information_schema` 未授权对象、函数副作用对象。
- 单连接跨库查询或 dblink 类能力（策略拒绝；DB 侧不授予）。

## 8. SQLGlot AST 策略

### 8.1 处理步骤

1. **接收**原始字符串；拒绝 `null`、非字符串、超长文本（建议上限 100 KiB 字符，超限 `rejected` / `POLICY_SQL_TOO_LARGE`）。
2. **解析**：SQLGlot、PostgreSQL dialect；解析失败 → `rejected` / `POLICY_PARSE_ERROR`。
3. **单语句**：解析结果必须恰好一条语句；多语句 → `POLICY_MULTI_STATEMENT`。
4. **形态允许列表**（在 AST 根与关键节点上判定）：
   - 允许：`SELECT`、`WITH ... SELECT`（非数据修改 CTE）、集合运算 `UNION`/`INTERSECT`/`EXCEPT`、子查询、JOIN、聚合、窗口函数。
   - 拒绝：`INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`DROP`/`TRUNCATE`/`COPY`/`CALL`/`DO`、数据修改 CTE、`SELECT INTO`、以及 AST 中出现的写/DDL 节点。
5. **对象访问范围**：
   - 收集表/CTE 引用；CTE 名允许；物理表必须属于授权业务表集合。
   - 限定 schema：仅允许未限定 schema 或显式 `public`；其他 schema 名拒绝。
   - 出现目录表、系统 schema、或未知表 → `POLICY_FORBIDDEN_OBJECT`。
6. **通过**：返回 allow；**不**改写 SQL 文本。

### 8.2 策略错误码

| 错误码 | 含义 |
| --- | --- |
| `POLICY_PARSE_ERROR` | 无法解析为合法 PostgreSQL 语句 |
| `POLICY_MULTI_STATEMENT` | 多语句 |
| `POLICY_FORBIDDEN_STATEMENT` | 语句形态不在允许集 |
| `POLICY_FORBIDDEN_OBJECT` | 对象/schema 越权 |
| `POLICY_SQL_TOO_LARGE` | 超过输入大小上限 |

### 8.3 与执行错误码

| 错误码 | 含义 |
| --- | --- |
| `EXECUTION_TIMEOUT` | 超过语句超时 |
| `EXECUTION_ROW_LIMIT` | 结果超过行数上限 |
| `EXECUTION_DATABASE_ERROR` | 其他数据库/驱动错误（摘要截断入库，避免过长） |

## 9. 最小 API 与错误语义

### 9.1 端点

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `GET` | `/health` | 进程存活；不检查数据库。成功 `200`。 |
| `GET` | `/ready` | 检查 platform 与 analytics 可连接，迁移版本满足，分析 seed 就绪标记或行数校验通过。成功 `200`；否则 `503`。 |
| `POST` | `/api/v1/query-runs` | 提交 SQL，同步完成治理链路，返回查询运行与结果或错误。 |
| `GET` | `/api/v1/query-runs/{id}` | 返回审计事实；`404` 若不存在。 |

### 9.2 统一响应包络

所有 JSON API（health/ready 可简化）使用：

```json
{
  "status": "succeeded | rejected | failed | running",
  "id": "uuid-or-null",
  "data": { },
  "error": {
    "code": "STABLE_CODE",
    "message": "human readable"
  }
}
```

约定：

- `error` 在 `succeeded` 时为 `null`；在 `rejected`/`failed` 时必填 `code` 与 `message`。
- `POST` 成功时 `data` 含：`columns`（`{ "name": string, "type": string }[]`）、`rows`（二维 JSON 值数组）、`row_count`、`duration_ms`。
- `POST` 拒绝/失败时 `data` 可仅含审计回显字段（`sql` 可选不回传以省流量；`id` 在顶层）。
- `GET` 的 `data` 含审计字段：`sql_text`、`status`、`row_count`、`duration_ms`、`created_at`、`finished_at`、`error`；**不含** `rows`。
- 请求体缺失 `sql` 或类型错误：HTTP `400`，可不创建查询运行；`code` 为 `REQUEST_INVALID`。
- 业务上的 `rejected`/`failed`：HTTP **`200`**（处理成功完成），由 `status` 区分；便于工作台统一解析且审计 ID 稳定返回。
- `running` 不应作为 `POST` 的最终响应（同步模型下响应发出前已到终态）；`GET` 可能读到残留 `running`。

### 9.3 数值与类型 JSON 映射

- `numeric` → JSON 字符串（避免 IEEE 精度丢失），或带约定的十进制字符串格式；前后端与测试共用同一约定。
- 时间戳 → ISO 8601 字符串。
- `null` 保留为 JSON `null`。

## 10. 查询工作台

### 10.1 行为

- 单页：SQL 多行输入、提交按钮、状态区、结果表或错误区。
- 提交后至响应返回前：展示执行中（对应一次 in-flight 请求，不依赖轮询；可选对已返回的 `id` 做一次 `GET` 校验，非必须）。
- 成功：渲染列头与行；展示行数与耗时。
- 拒绝/失败：展示稳定错误码与可读说明；若有 `id` 可展示便于排障。
- 不提供图表、保存查询列表、用户切换、主题运营配置。

### 10.2 技术

- Node.js 24、React 19、TypeScript、Vite。
- 通过环境注入的 API 基址访问后端（浏览器侧指向可配置的 API 宿主端口）。
- 组件级逻辑可用 Vitest；主流程用 Playwright。

## 11. 迁移与幂等 seed

### 11.1 platform

- Alembic 管理；首迁创建 `query_runs` 与必要索引（`created_at` 可选）。
- 重复 `upgrade head` 必须安全无破坏。

### 11.2 analytics

- 迁移创建五张表，列类型/空性/主键/唯一/外键与 `contract.json` 对齐：
  - `customers`、`product_categories`、`products`、`orders`、`order_items`
  - `order_items` 唯一约束 `(order_id, product_id)`
  - 金额列 `numeric`；`discount_rate` 范围由契约与校验器保证，DB 可加 check 但非必须
- **不**创建订单总额冗余列。

### 11.3 seed 幂等

- 数据源：仓库内已提交的 `datasets/sales-analytics-v1/data/*.csv`（权威 fixture），不是临时随机生成。
- 推荐算法：在迁移角色下，按外键顺序 `TRUNCATE ... CASCADE`（或等价清空）后批量 COPY/插入 CSV；或按主键 upsert 使最终行与 CSV 一致。
- 重复执行 seed 后：行数等于契约 `expected_counts`，内容与 fixture 一致；不得叠加重复行。
- 可写 `analytics_seed_meta(dataset, version, applied_at)` 供 `/ready` 快速判断；若采用行数校验，须与契约一致。
- 生成器 `generate.py` 用于维护数据集，**不是**运行时 seed 的必需路径；运行时以 CSV + manifest 为准。

### 11.4 启动顺序

1. 启动 PostgreSQL，创建逻辑库与角色（幂等脚本）。
2. platform 迁移 → analytics 迁移 → analytics seed。
3. 启动 API，等待 `/ready`。
4. 启动 Web。

统一命令封装上述顺序；失败则非零退出。

## 12. Compose 并行隔离

| 项 | 规则 |
| --- | --- |
| 项目名 | `COMPOSE_PROJECT_NAME` 可配，默认可由目录名派生 |
| Web/API 端口 | `WEB_HOST_PORT`、`API_HOST_PORT` 可配并映射到容器内固定服务端口 |
| 数据库端口 | **不要求**固定映射到宿主；API/Web 容器网络内访问即可 |
| 命名 | 禁止固定 `container_name`；禁止全局固定网络名/卷名；使用 Compose 默认项目前缀 |
| 配置 | `.env.example` 列出可选项；真实 `.env` 不提交 |
| 数据 | 每项目独立命名卷；禁止多个实例共享同一绑定数据目录作为默认 |

## 13. 失败、安全与迁移

### 13.1 失败模式

| 失败 | 用户可见 | 审计 | 安全影响 |
| --- | --- | --- | --- |
| 策略拒绝 | `rejected` + 错误码 | 是 | 不执行 |
| 超时/行数 | `failed` + 错误码 | 是 | 只读执行被中止 |
| DB 宕机 | `/ready` 失败；POST 可能 `failed` 或 503 | 尽量记录 | 不降级为跨身份重试 |
| 迁移未完成 | `/ready` 503；工作台应显示不可用 | 不建业务查询 | 防止半初始化写入 |

### 13.2 安全原则

- 默认拒绝：未知 AST 形态、未知对象，拒绝。
- 双边界：策略缺陷时只读角色仍应挡住写入与越权表。
- 错误消息可含语法类别，避免回显完整服务器内部堆栈给浏览器。
- 首轮无鉴权仅适用于可信本地环境；文档与 README 需标明勿裸奔公网。

### 13.3 数据迁移与回滚

- 分析数据以版本化 fixture 重建为主，不做复杂在线迁移。
- platform schema 仅向前迁移；破坏性变更需新 Alembic 修订。
- 回滚本地环境：删卷后全量再起，而不是依赖生产级 down 迁移。

## 14. 测试接缝与验证原则

测试通过公共边界观察行为，不依赖私有函数钩子。

### 14.1 单元（pytest）：SqlPolicy

- **接缝**：`SqlPolicy.check(sql: str) -> PolicyResult`。
- **覆盖**：允许的 SELECT/CTE/集合运算/窗口；拒绝多语句、写操作、DDL、`SELECT INTO`、修改型 CTE、非法对象、系统目录、超大 SQL、解析失败。
- **不启** 数据库亦可运行。

### 14.2 集成（pytest）：双库与执行器

- **接缝**：真实或 Compose 启动的 PostgreSQL；platform 仓储 + analytics 只读执行；完整 `QueryRunService` 或 API 客户端。
- **证明**：
  - `platform_app` 可写审计，且**不能**用该身份成功执行对业务表的用户写操作测试路径（或执行器拒绝使用该 DSN）。
  - `analytics_readonly` 对授权表 SELECT 成功；对写操作或未授权表失败。
  - 策略拒绝不在分析侧产生业务数据变化。
  - 超时与行数上限映射到 `failed` 与稳定错误码。
  - seed 后行数符合契约。

### 14.3 浏览器（Playwright）：工作台主流程

- **接缝**：用户可见 UI + 真实 API（测试环境 Compose）。
- **覆盖**：输入合法 SQL → 见执行中 → 见结果表；输入非法 SQL → 见拒绝信息；服务未就绪时的失败提示（若易测）。
- Vitest 仅覆盖纯 UI 状态函数或组件，不替代 Playwright 主流程。

### 14.4 统一命令

- 一条命令启动依赖并跑全部分层测试（具体脚本名实现阶段确定）。
- 数据集仓库校验保持：`cd datasets/sales-analytics-v1 && python3 validate.py`。

### 14.5 证据类型

| 类型 | 用途 |
| --- | --- |
| 机械 | 命令退出码、策略单测、契约 validate |
| 行为 | 集成与 Playwright 展示的状态与结果 |
| 共识 | 本文 + background + contract 三者无矛盾 |

## 15. 延后决策

| 项 | 触发条件 |
| --- | --- |
| 异步查询与队列 | 同步超时无法满足真实查询时长 |
| 结果集持久化/导出 | 审计回放或合规需要保留网格 |
| 终端用户鉴权与 RBAC | 部署出本地可信环境或多用户场景 |
| `running` 残留回收 | 生产化或长时间运行后异常行增多 |
| 语义层/指标目录 | 业务需要统一指标定义而非自由 SQL |
| 只读副本或资源隔离增强 | 并发与吵闹邻居问题出现 |
| SQLGlot 版本钉死策略与升级 | 依赖引入后在锁文件与 changelog 维护 |

## 16. 深模块自检（摘要）

| 模块 | 最小外部接口 | 隐藏复杂度 | 不变量 |
| --- | --- | --- | --- |
| SqlPolicy | `check(sql)` | 方言解析、AST 遍历 | 默认拒绝未知形态/对象 |
| QueryExecutor | `execute(sql) -> rows\|error` | 超时、行数、驱动错误映射 | 只用只读 DSN |
| QueryRunRepository | `create` / `finalize` / `get` | SQLAlchemy 细节 | 只碰 platform |
| QueryRunService | `submit` / `get` | 事务与状态迁移 | 先审计再执行；终态不可回退 |
| Web 工作台 | 用户事件 → HTTP | 渲染与加载态 | 不直连 DB |
