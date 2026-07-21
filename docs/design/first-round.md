# 首轮设计：显式 SQL 受控执行链路

本文件是 DecisionHarbor 首轮范围的权威设计。术语见 [`CONTEXT.md`](CONTEXT.md)；需求与约束见 [`../background/`](../background/)；固定数据契约见 [`../../datasets/sales-analytics-v1/contract.json`](../../datasets/sales-analytics-v1/contract.json)。本设计不改写背景与契约，只在其约束内确定可实现的行为与边界。

## 1. 范围与目标 / 非目标

### 目标

1. 在只安装 Git、Docker、Docker Compose 的干净 WSL 环境中，用一条统一命令完成构建、启动 Web/API/PostgreSQL、创建双逻辑库与身份、迁移与 seed、等待健康与就绪。
2. 受治理 SQL 查询执行：基于 SQLGlot AST 与对象访问范围做策略判定，用 `analytics` 只读身份执行允许的查询。
3. 最小查询工作台：输入 SQL、提交、展示执行中状态、展示结果表格或拒绝/失败信息。
4. 每次提交产生一条稳定的查询记录，保存原始 SQL、策略判定或拒绝原因、执行状态、返回行数、耗时、错误摘要与创建时间。
5. 一条统一测试命令，覆盖 SQL 策略单元、双数据库集成与浏览器主流程。

### 非目标（首轮不做）

- 自然语言转 SQL、LLM、RAG、MCP、A2A。
- 用户注册、复杂 RBAC、运营后台、图表编辑器、前端视觉专项。
- 用户认证与审计中的可信用户身份（首轮无认证；见 §10 决策 8）。
- 跨库访问 platform 状态，或用 platform 写入身份执行用户 SQL。
- 结果持久化、分页、导出、查询取消与异步队列（见 §12 延后决策）。

## 2. 模块与数据流

### 模块

| 模块 | 技术栈 | 职责 |
| --- | --- | --- |
| Web | React 19 + TypeScript + Vite（Node 24） | 最小查询工作台；不承担策略或审计 |
| API | Python 3.13 + FastAPI + Pydantic | 策略判定、查询执行编排、审计读写、`/health` `/ready` |
| 策略 | SQLGlot（API 内纯函数模块） | 解析为 AST、语句类型判定、对象访问范围校验 |
| 执行器 | SQLAlchemy 2.0 + psycopg 3 | 以 `analytics` 只读身份执行允许的 SQL，施加资源限制 |
| 审计 | SQLAlchemy 2.0 + Alembic（platform 库） | 读写 `query_runs` 审计记录 |
| 数据库 | PostgreSQL 18（单容器双逻辑库） | `platform` 审计 + `analytics` 固定数据 |
| 数据集 | `datasets/sales-analytics-v1/` | 固定产品输入，迁移与 seed 的来源 |

### 数据流（单次提交）

1. 用户在工作台输入 SQL，`POST /api/v1/query-runs` 请求体 `{ "sql": "..." }`。
2. API 以 platform 写入身份在 `platform.query_runs` 创建记录，状态 `pending`。
3. 策略模块 `analyze(sql)` 解析为 AST 并校验（见 §5）：
   - 解析失败 → 记录 `rejected` + `PARSE_ERROR`，返回拒绝响应。
   - 多语句 / 禁止语句 / 数据修改 CTE / `SELECT INTO` / 对象越界 → 记录 `rejected` + 对应错误码，返回拒绝响应。
4. 策略通过 → 记录状态 `running`；执行器以 `analytics` 只读身份连接，设置语句超时与行数上限，执行 SQL（见 §5、§4）。
5. 执行结果：
   - 成功 → 记录 `succeeded` + `row_count` + `duration_ms`，返回列定义、行数据、行数、耗时。
   - 超时 → 记录 `failed` + `STATEMENT_TIMEOUT`。
   - 超行数上限 → 记录 `failed` + `ROW_LIMIT_EXCEEDED`。
   - 其他数据库错误 → 记录 `failed` + `EXECUTION_ERROR`。
6. API 将终态事实写回 `query_runs`，返回统一 JSON 响应。
7. 工作台展示结果表格或拒绝/失败信息。

> 执行采用同步模型：在单个 `POST` 请求内完成策略判定与执行，`pending`→`running`→终态在服务端记录中流转；`GET /api/v1/query-runs/{id}` 在长执行期间可观察到 `running`。理由见 §10 决策 1。

## 3. 查询运行状态与审计事实

### 状态机

```text
pending ──┬─> rejected        （策略拒绝，未执行）
          ├─> running ──┬─> succeeded
          │             ├─> failed（STATEMENT_TIMEOUT | ROW_LIMIT_EXCEEDED | EXECUTION_ERROR）
          └─> failed（INTERNAL_ERROR，服务端异常）
```

终态至少区分 `succeeded`、`rejected`、`failed`；`pending`、`running` 为瞬态。每次提交恰好产生一条记录并到达终态。

### 审计表 `platform.query_runs`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 由应用生成 |
| `sql_text` | text not null | 原始 SQL |
| `status` | varchar not null | `pending`/`running`/`succeeded`/`rejected`/`failed` |
| `error_code` | varchar null | 终态为 `rejected`/`failed` 时的稳定错误码 |
| `error_message` | text null | 可读错误摘要 |
| `row_count` | integer null | 成功时返回行数 |
| `duration_ms` | integer null | 策略通过后执行耗时 |
| `created_at` | timestamptz not null | 记录创建时间 |
| `finished_at` | timestamptz null | 到达终态时间（最小扩展，便于审计） |

### 审计事实边界

- 审计只保存事实（状态、错误码、行数、耗时、时间），**不保存结果行数据**。理由见 §10 决策 4。
- 拒绝的查询同样落库（`rejected` + 错误码 + 原因），满足“为每次策略判定、拒绝或执行结果保留稳定记录”。
- 用户 SQL 永不接触 `platform`；审计写入只用 platform 写入身份。

## 4. 双数据库与只读身份边界

### 逻辑库与身份

| 逻辑库 | 用途 | 使用身份 | 权限 |
| --- | --- | --- | --- |
| `platform` | 查询审计等平台状态 | platform 写入身份 | 建库建表、`query_runs` 的 INSERT/UPDATE/SELECT |
| `analytics` | 固定销售分析数据 | analytics 只读身份 | `analytics` schema 的 USAGE + 五张表的 SELECT |

两个身份是独立的 PG role，API 持有两个独立连接池，永不交叉。

### 只读身份（第二道边界）

- analytics 只读身份：`USAGE` 于 `analytics` schema，`SELECT` 于 `customers`、`product_categories`、`products`、`orders`、`order_items`；显式 `REVOKE` 写权限；无 `platform` schema 访问权。
- 应用层策略是第一道边界（AST + 对象范围），数据库只读身份是第二道边界；二者防御纵深，互不替代。
- 禁止以任何方式绕过只读身份执行用户 SQL（如切换到 platform 写入身份或超管身份）。

### platform 写入身份

- 仅访问 `platform`，不访问 `analytics`；API 不通过写入身份执行用户 SQL。
- 审计 schema 由应用自行设计并通过 Alembic 迁移建立（见 §8）。

## 5. SQL 策略（SQLGlot AST）与资源限制

### 策略模块接口（纯函数，无 I/O）

```text
analyze(sql: str) -> PolicyResult
PolicyResult = {
  allowed: bool,
  violations: list[ErrorCode],
  object_scope: list[(schema, table)]
}
```

以 PostgreSQL 方言解析。任何解析失败或无法判定的对象 → `allowed=False`（fail-closed）。

### 判定步骤

1. **解析**：SQLGlot 解析为 AST；失败 → `PARSE_ERROR`。
2. **多语句**：解析得到多条语句 → `MULTI_STATEMENT`。
3. **语句类型**：仅允许以 `SELECT` 为根的只读表达式——`SELECT`、`WITH ... SELECT`、连接、子查询、聚合、窗口函数、`UNION`/`INTERSECT`/`EXCEPT`。出现 `INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`DROP`/`TRUNCATE`/`COPY`/`CALL`/`DO` → `FORBIDDEN_STATEMENT`。
4. **数据修改型 CTE 与 `SELECT INTO`**：CTE 含 `INSERT`/`UPDATE`/`DELETE` → `DATA_MODIFYING_CTE`；`SELECT INTO` → `SELECT_INTO`。
5. **对象访问范围**：遍历 AST 中所有表引用（`exp.Table`），解析 schema（默认 `analytics`）；非 `analytics` schema、非五张允许表、系统目录（`pg_*`、`information_schema`）或未授权对象 → `FORBIDDEN_OBJECT`。
6. `allowed = (violations 为空)`。

允许表集合固定为契约中的五张表（见 `contract.json`），不随实现改名。

### 资源限制（执行器）

| 限制 | 环境变量 | 默认 | 行为 |
| --- | --- | --- | --- |
| 语句超时 | `DH_STATEMENT_TIMEOUT_MS` | 30000 | 只读会话 `SET statement_timeout` |
| 结果行数上限 | `DH_ROW_LIMIT` | 1000 | 取 `N+1` 行，若返回 > `N` 则 `ROW_LIMIT_EXCEEDED` |

- 行数上限采用 fail-closed：超过上限不返回截断结果，记录 `failed` + `ROW_LIMIT_EXCEEDED`。理由见 §10 决策 3。
- 资源限制只作用于通过策略的查询，施加在 `analytics` 只读会话上。

## 6. 最小 API 与错误语义

### 端点

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| `GET` | `/health` | 进程存活，恒 200 |
| `GET` | `/ready` | 仅当数据库连通且迁移完成时 200，否则 503 |
| `POST` | `/api/v1/query-runs` | 提交 `{ "sql": "..." }`，返回终态查询记录 |
| `GET` | `/api/v1/query-runs/{id}` | 读取查询记录的状态与审计事实（不含结果行） |

### 统一响应体

```text
{
  "id": "<query_run_id>",
  "status": "succeeded" | "rejected" | "failed",
  "sql": "<原始 SQL>",
  "error_code": "<稳定码>",        // rejected/failed 时存在
  "error_message": "<可读说明>",    // rejected/failed 时存在
  "columns": [{ "name": "...", "type": "..." }],  // succeeded 时存在
  "rows": [[...]],                 // succeeded 时存在；GET 省略
  "row_count": N,                  // succeeded 时存在
  "duration_ms": M,
  "created_at": "<ISO8601>"
}
```

`GET` 返回同一信封但 `rows` 省略（结果不持久化），`row_count`/`duration_ms`/`status`/`error_*` 保留。

### HTTP 状态映射

| 结果 | HTTP | 说明 |
| --- | --- | --- |
| `succeeded` | 200 | 查询成功 |
| `rejected` | 422 | 策略拒绝（请求可理解但内容被拒） |
| `failed`（`STATEMENT_TIMEOUT`/`ROW_LIMIT_EXCEEDED`/`EXECUTION_ERROR`） | 200 | 查询已执行但失败，属合法终态 |
| `failed`（`INTERNAL_ERROR`） | 500 | 服务端异常 |
| `GET` 未找到 | 404 | 记录不存在 |

响应体 `status` 字段是权威信号；HTTP 码为辅助。理由见 §10 决策 7。

### 稳定错误码

| 码 | 阶段 | 终态 |
| --- | --- | --- |
| `PARSE_ERROR` | 策略 | `rejected` |
| `MULTI_STATEMENT` | 策略 | `rejected` |
| `FORBIDDEN_STATEMENT` | 策略 | `rejected` |
| `DATA_MODIFYING_CTE` | 策略 | `rejected` |
| `SELECT_INTO` | 策略 | `rejected` |
| `FORBIDDEN_OBJECT` | 策略 | `rejected` |
| `STATEMENT_TIMEOUT` | 执行 | `failed` |
| `ROW_LIMIT_EXCEEDED` | 执行 | `failed` |
| `EXECUTION_ERROR` | 执行 | `failed` |
| `INTERNAL_ERROR` | 服务端 | `failed` |

## 7. 查询工作台

- 单页：SQL 输入框、提交按钮、状态区、结果区。
- 状态区展示 `idle` / `submitting` / `running`（请求进行中的客户端指示）与终态。
- 结果区：成功展示列定义 + 行表格；`rejected`/`failed` 展示稳定错误码 + 可读说明 + 记录 id。
- 调用 `POST /api/v1/query-runs`，按响应渲染。
- 不做视觉专项、图表、历史列表或导出（非目标）。
- Web 通过 Vite 构建；在 Compose 中作为独立服务，`/api` 反代到 API 服务。

## 8. 迁移与幂等 seed

### platform（应用自有 schema，Alembic）

- Alembic 管理 `platform` 的 `query_runs` 表与后续演进；`alembic upgrade head` 通过版本号幂等。
- schema 由应用设计，不是外部固定输入。

### analytics（固定外部输入，幂等 seed 脚本）

- 由独立 seed 脚本建立，**不纳入 Alembic**。理由见 §10 决策 5。
- `CREATE SCHEMA IF NOT EXISTS analytics`；按 `contract.json` 的表与字段类型 `CREATE TABLE IF NOT EXISTS`（字段名与类型严格遵循契约，不改写）。
- 数据从 `datasets/sales-analytics-v1/data/*.csv` 加载，按外键依赖顺序（`product_categories` → `customers` → `products` → `orders` → `order_items`）。
- 幂等加载：重复运行收敛到契约的规范状态，不累积重复行、不报错（如 `TRUNCATE ... CASCADE` 后 `COPY`，或 `INSERT ... ON CONFLICT`）。
- seed 脚本以 platform 之外的管理身份一次性建库建表建角色（见 §9），再以具备 `analytics` 写权限的身份灌数据；与运行期只读身份分离。

### 统一命令与就绪

- 一条统一命令（如 `make up`）依次：构建镜像 → 启动 web/api/db → 建逻辑库与身份 → 运行 Alembic + seed → 等待 `/health` 与 `/ready` 成功。
- `/ready` 仅在数据库连通且迁移完成后返回 200；迁移与 seed 在就绪前完成。
- 重复运行 `make up` / 迁移 / seed 不得产生破坏性重复（Alembic 版本号幂等；analytics seed 收敛到规范状态）。
- 一条统一测试命令（如 `make test`）运行 pytest + Vitest + Playwright。

## 9. Compose 并行隔离

| 配置 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| Compose 项目名 | `DH_PROJECT_NAME` | `decisionharbor` | 通过 `COMPOSE_PROJECT_NAME` 作用域化所有资源 |
| Web 宿主端口 | `DH_WEB_PORT` | 5173 | 可配置 |
| API 宿主端口 | `DH_API_PORT` | 8000 | 可配置 |

- 不使用固定 `container_name`；网络与数据卷使用 Compose 项目相关默认命名，实现每实例独立命名空间。
- 数据库不暴露固定宿主端口；服务间通过服务名（如 `db`）在 Compose 内部网络访问。
- 不使用共享绑定目录或全局网络/卷名。
- 提交 `.env.example` 记录可配置项与默认值；`.env` 本身不提交。
- 单 PG 容器提供 `platform` 与 `analytics` 双逻辑库；init 阶段创建两库与两个 role 并授权。

## 10. 关键决策与理由

影响安全或外部行为的决策：

1. **同步执行，无 worker 队列**（外部行为）：`POST` 内完成判定与执行，`running` 为服务端瞬态。理由：首轮最小化；“执行中状态”由服务端瞬态 + 客户端加载指示满足；单条短查询无需队列，异步/取消延后。
2. **解析失败即拒绝**（安全）：无法得到 AST 则无法验证安全性 → `rejected` + `PARSE_ERROR`，fail-closed。
3. **行数超限 fail-closed**（安全/外部行为）：取 `N+1` 行，超限即 `failed` + `ROW_LIMIT_EXCEEDED`，不返回截断结果，避免误导。
4. **结果行不持久化**（边界/安全）：审计只存事实；`platform` 不长期保存 `analytics` 派生数据，保持双库职责清晰，且避免大结果存储。
5. **analytics 用幂等 seed 脚本，platform 用 Alembic**（边界）：analytics schema 是固定外部产品输入，非应用自有演进对象；Alembic 只管应用自有的 `platform`。
6. **双身份 + 数据库权限为第二道边界**（安全）：应用策略与只读 role 防御纵深，任一层独立生效。
7. **HTTP 码辅助、`status` 字段权威**（外部行为）：`succeeded` 200、`rejected` 422、执行类 `failed` 200（合法终态）、`INTERNAL_ERROR` 500；统一 JSON 优先。
8. **首轮无认证/RBAC**（范围）：为明确非目标；审计记录 SQL 与事实，不含可信用户身份。
9. **资源限制默认值可配置**（外部行为）：需求未给数值，取安全默认（超时 30s、行上限 1000）并经环境变量覆盖。
10. **Compose 项目名 + 端口可配置、无固定名**（外部行为）：满足多工作区并行隔离的硬约束。

## 11. 失败模式与不变量

### 不变量

- 用户 SQL 永不以 platform 写入身份执行，永不接触 `platform`。
- 用户 SQL 永不访问非 `analytics` 五张表以外的对象。
- 每次提交恰好产生一条 `query_runs` 记录并到达终态。
- 重复迁移与 seed 收敛到规范状态，不累积重复、不破坏。
- 双库身份边界在运行期始终生效。

### 失败模式

| 失败 | 处理 | 终态/码 |
| --- | --- | --- |
| SQL 不可解析 | 策略拒绝 | `rejected`/`PARSE_ERROR` |
| 多语句 / 禁止语句 / 数据修改 CTE / `SELECT INTO` / 对象越界 | 策略拒绝 | `rejected`/对应码 |
| 语句超时 | 终止执行 | `failed`/`STATEMENT_TIMEOUT` |
| 超行数上限 | 终止执行 | `failed`/`ROW_LIMIT_EXCEEDED` |
| 数据库执行错误 | 捕获 | `failed`/`EXECUTION_ERROR` |
| 服务端异常 | 兜底 | `failed`/`INTERNAL_ERROR` |

## 12. 延后决策

- 用户认证、RBAC 与审计中的可信用户身份。
- 异步执行队列、查询取消、长查询进度。
- 结果持久化、分页、导出。
- 自然语言转 SQL、LLM、语义层、RAG、MCP、A2A。
- 运营后台、图表编辑器、前端视觉专项。
- 多租户与跨库 platform 状态访问。

## 13. 测试接缝

### 单元（pytest）— 策略模块

针对纯函数 `analyze(sql)`，无数据库：

- 允许：`SELECT`、`WITH ... SELECT`、连接、子查询、聚合、窗口、`UNION`/`INTERSECT`/`EXCEPT`（均作用于五张允许表）。
- 拒绝：多语句；`INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`DROP`/`TRUNCATE`/`COPY`/`CALL`/`DO`；数据修改型 CTE；`SELECT INTO`；非 `analytics` 表；系统目录；不可解析 SQL。
- 对象范围：跨 schema、未授权对象、默认 schema 解析。

### 集成（pytest，against PostgreSQL）

- 身份分离：analytics 只读身份可 SELECT 五张表，不可 INSERT/UPDATE/DELETE/TRUNCATE，不可访问 `platform`；platform 写入身份可写读 `query_runs`。
- 端到端（TestClient + 真库）：允许查询 → 200 `succeeded` 且记录落库；禁止查询 → 422 `rejected` 且记录落库；失败查询 → `failed` 且记录落库。
- 迁移与 seed 幂等：运行两次，行数与 `contract.json` 的 `expected_counts` 一致，无重复，`platform` schema 完整。

### 浏览器（Playwright）— 主流程

- 加载工作台，输入允许 SQL，提交，见结果表格。
- 输入禁止 SQL（如 `DELETE FROM customers`），提交，见拒绝信息与稳定错误码。
- 输入多语句，提交，见拒绝信息。
