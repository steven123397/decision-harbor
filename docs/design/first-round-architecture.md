# 首轮架构设计

## 范围

本文档覆盖 DecisionHarbor 首轮实现的完整架构：模块划分、数据流、查询运行生命周期、双数据库边界、SQL 策略引擎、HTTP API、前端工作台、迁移与 seed、本地 Compose 隔离和测试接缝。

不覆盖：自然语言转 SQL、LLM/RAG/MCP/A2A、用户注册与复杂 RBAC、运营后台、图表编辑器、多租户、生产部署。

## 目标与非目标

### 目标

- 在只安装 Git、Docker 和 Docker Compose 的干净 WSL 环境中一条命令启动全栈。
- 基于 SQLGlot AST 的 SQL 策略校验，拒绝非只读或非授权对象的查询。
- 使用独立只读数据库身份执行用户 SQL，与应用写入身份物理隔离。
- 提供最小查询工作台：输入 SQL、提交、展示结果表格或拒绝原因。
- 每次查询保留完整审计记录。
- 多个本地工作区可并行运行，互不干扰。

### 非目标

- 异步查询队列或后台任务。
- 用户认证、会话管理或权限分级。
- 查询缓存、物化视图或语义层。
- 可视化图表、导出或分享。
- 生产级高可用、水平扩展或监控告警。

## 术语与场景

| 术语 | 定义 |
| --- | --- |
| 查询运行（QueryRun） | 一次用户 SQL 提交及其完整生命周期记录。 |
| 策略判定（Policy Verdict） | 策略引擎对 SQL 的允许或拒绝结论。 |
| 分析库（analytics） | 承载固定销售数据的只读逻辑数据库。 |
| 平台库（platform） | 承载查询审计等产品状态的可写逻辑数据库。 |
| 只读身份（analytics_reader） | 执行用户 SQL 的数据库角色，仅有 analytics 的 SELECT 权限。 |
| 平台身份（platform_app） | API 服务连接平台库的数据库角色，拥有 platform 的读写权限。 |

### 核心场景

1. 用户在工作台输入 `SELECT region, SUM(...) FROM ... GROUP BY region`，提交。
2. API 接收 SQL，策略引擎解析 AST 并校验通过。
3. 执行器使用 analytics_reader 身份在 analytics 库执行查询。
4. 结果返回前端渲染为表格；审计记录写入 platform 库。
5. 若 SQL 包含 `DELETE`，策略引擎拒绝，前端展示拒绝原因，审计记录同样写入。

## 不变量

1. 用户 SQL 永远不以平台身份执行；数据库权限是应用策略之外的第二道边界。
2. 每条提交的 SQL 无论成功、拒绝或失败，都产生且仅产生一条审计记录。
3. 策略校验基于 AST，不依赖字符串黑名单。
4. analytics 库中不存在应用写入路径；seed 使用独立管理身份。
5. 金额使用定点数（`numeric`），`discount_rate` 范围 [0, 1]，订单总额不作为冗余字段存储。
6. 迁移和 seed 幂等：重复执行不产生破坏性重复。

## 模块边界

```
┌─────────────────────────────────────────────────────────┐
│  Web (React 19 + Vite + TypeScript)                     │
│  查询工作台：SQL 输入、提交、状态展示、结果表格          │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP (JSON)
┌────────────────────────▼────────────────────────────────┐
│  API (Python 3.13 + FastAPI + Pydantic)                  │
│                                                          │
│  ┌────────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │ Router     │→ │ Policy      │→ │ Executor         │  │
│  │ /api/v1/   │  │ Engine      │  │ (analytics_reader)│  │
│  │ query-runs │  │ (SQLGlot)   │  │ 语句超时+行数上限 │  │
│  └────────────┘  └─────────────┘  └──────────────────┘  │
│         │                                                │
│         ▼                                                │
│  ┌────────────────────┐                                  │
│  │ Audit Repository   │                                  │
│  │ (platform_app)     │                                  │
│  └────────────────────┘                                  │
└─────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  PostgreSQL 18 (单容器，双逻辑库)                        │
│                                                          │
│  ┌──────────────────┐     ┌──────────────────────────┐  │
│  │ platform         │     │ analytics                 │  │
│  │ - query_runs     │     │ - customers              │  │
│  │ (platform_app    │     │ - product_categories     │  │
│  │  读写)           │     │ - products               │  │
│  │                  │     │ - orders                  │  │
│  │                  │     │ - order_items             │  │
│  │                  │     │ (analytics_reader 只读)   │  │
│  └──────────────────┘     └──────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 模块职责

| 模块 | 职责 | 技术 |
| --- | --- | --- |
| Web | SQL 输入、提交、状态轮询或同步等待、结果/拒绝展示 | React 19, TypeScript, Vite |
| Router | 请求校验、响应序列化、错误映射 | FastAPI, Pydantic |
| Policy Engine | SQL 解析、语句类型校验、对象范围校验、资源限制配置 | SQLGlot |
| Executor | 使用只读身份执行允许的 SQL，应用语句超时和行数上限 | psycopg 3, SQLAlchemy 2.0 |
| Audit Repository | 查询运行的持久化读写 | SQLAlchemy 2.0, Alembic |
| Migrations | platform 结构迁移；analytics 表结构建立 | Alembic |
| Seed | 将 CSV 数据幂等填充到 analytics | 管理身份 (analytics_admin) |

## 查询运行生命周期

### 状态机

```
submitted → rejected   (策略拒绝)
submitted → succeeded  (执行成功)
submitted → failed     (执行错误)
```

首轮采用同步模型：POST 请求阻塞直到查询完成或拒绝，响应即最终状态。不引入 `pending`/`running` 中间态。

### 审计记录（query_runs 表）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | bigint PK | 自增主键 |
| raw_sql | text NOT NULL | 用户提交的原始 SQL |
| status | varchar(20) NOT NULL | `succeeded` / `rejected` / `failed` |
| reject_code | varchar(40) | 策略拒绝时的稳定错误码 |
| reject_reason | text | 人类可读的拒绝说明 |
| error_summary | text | 执行失败时的错误摘要 |
| row_count | integer | 成功时返回的行数 |
| columns_json | jsonb | 成功时的列定义 `[{name, type}]` |
| duration_ms | integer | 执行耗时（毫秒），拒绝时为空 |
| created_at | timestamptz NOT NULL | 提交时间，默认 `now()` |

结果行数据不持久化到审计记录；仅返回给调用方。

## 双数据库与身份边界

### 逻辑数据库

| 数据库 | 用途 | 访问身份 |
| --- | --- | --- |
| platform | 查询审计（query_runs）| platform_app（读写） |
| analytics | 固定销售分析数据 | analytics_reader（只读）、analytics_admin（seed/迁移） |

### 数据库角色

| 角色 | 权限 | 使用者 |
| --- | --- | --- |
| platform_app | platform 库 ALL PRIVILEGES | API Audit Repository |
| analytics_reader | analytics 库 SELECT ONLY | API Executor |
| analytics_admin | analytics 库 ALL PRIVILEGES | 迁移与 seed 脚本 |

### 安全决策

| 决策 | 结论 | 理由 |
| --- | --- | --- |
| 用户 SQL 的执行身份 | analytics_reader，仅 SELECT | 即使策略引擎被绕过，数据库层仍阻止写入和非授权对象访问。 |
| 平台库与分析库隔离 | 两个逻辑数据库，不同角色 | 防止用户 SQL 读取或干扰审计记录；角色粒度隔离优于 schema 隔离。 |
| seed 使用独立管理身份 | analytics_admin | 应用运行期不持有分析库写权限；seed 完成后该身份不参与请求链路。 |
| 不在 API 进程中暴露管理身份 | 迁移/seed 由独立脚本执行 | 减少攻击面；API 进程仅持有 platform_app 和 analytics_reader 连接。 |

## SQL 策略引擎

### 校验流程

```
原始 SQL
  │
  ▼
SQLGlot 解析 (dialect=postgres)
  │ 解析失败 → 拒绝 (PARSE_ERROR)
  ▼
语句数量检查
  │ >1 → 拒绝 (MULTI_STATEMENT)
  ▼
语句类型检查
  │ 非 SELECT/WITH...SELECT → 拒绝 (FORBIDDEN_STATEMENT)
  ▼
数据修改型 CTE 检查
  │ WITH 内含 INSERT/UPDATE/DELETE → 拒绝 (FORBIDDEN_CTE)
  ▼
SELECT INTO 检查
  │ → 拒绝 (FORBIDDEN_INTO)
  ▼
对象范围检查
  │ 引用的表/视图不在允许列表 → 拒绝 (FORBIDDEN_OBJECT)
  │ 引用系统目录 (pg_*, information_schema) → 拒绝 (FORBIDDEN_OBJECT)
  ▼
允许 → 交给 Executor
```

### 允许的对象列表

```
analytics.customers
analytics.product_categories
analytics.products
analytics.orders
analytics.order_items
```

### 资源限制

| 限制 | 默认值 | 配置方式 | 理由 |
| --- | --- | --- | --- |
| 语句超时 | 30 秒 | 环境变量 `QUERY_STATEMENT_TIMEOUT_MS` | 防止长查询占用分析库资源。 |
| 结果行数上限 | 1000 行 | 环境变量 `QUERY_MAX_ROWS` | 防止超大结果集导致 API 内存溢出。 |

行数上限通过 `LIMIT` 注入实现：若用户 SQL 未含 LIMIT 或 LIMIT 超过上限，执行器在 AST 层面追加/收紧 LIMIT。

### 策略决策

| 决策 | 选项 | 结论 | 理由 |
| --- | --- | --- | --- |
| 解析器 | SQLGlot vs pglast vs 正则 | SQLGlot | 背景指定；纯 Python、支持 Postgres 方言、可遍历 AST 节点。 |
| 对象范围校验粒度 | 字符串匹配 vs AST 表引用提取 | AST 表引用提取 | 字符串匹配可被别名、引号、注释绕过；AST 提取覆盖 CTE、子查询和 JOIN。 |
| LIMIT 注入位置 | 字符串拼接 vs AST 修改 | AST 修改后生成 SQL | 保持与解析一致的路径，避免注入风险。 |

## 最小 API

### 端点

| 方法 | 路径 | 用途 | 成功状态码 |
| --- | --- | --- | --- |
| GET | /health | 进程存活 | 200 |
| GET | /ready | 数据库和迁移就绪 | 200 |
| POST | /api/v1/query-runs | 提交 SQL 并同步返回结果 | 201 |
| GET | /api/v1/query-runs/{id} | 读取指定查询记录 | 200 |

### 响应结构

所有响应使用统一 JSON 信封：

```json
{
  "status": "succeeded" | "rejected" | "failed" | "error",
  "data": { ... },
  "error": { "code": "...", "message": "..." }
}
```

- `succeeded`：`data` 包含 `id`, `columns`, `rows`, `row_count`, `duration_ms`, `created_at`。
- `rejected`：`error` 包含稳定 `code` 和可读 `message`，`data` 包含 `id`, `created_at`。
- `failed`：`error` 包含 `code` 和 `message`（错误摘要），`data` 包含 `id`, `created_at`。
- `error`：请求级错误（如 JSON 格式无效、SQL 字段缺失），不产生审计记录。

### 错误码

| 错误码 | 含义 | HTTP 状态码 |
| --- | --- | --- |
| PARSE_ERROR | SQL 解析失败 | 201 (rejected) |
| MULTI_STATEMENT | 包含多条语句 | 201 (rejected) |
| FORBIDDEN_STATEMENT | 非 SELECT 语句类型 | 201 (rejected) |
| FORBIDDEN_CTE | 数据修改型 CTE | 201 (rejected) |
| FORBIDDEN_INTO | SELECT INTO | 201 (rejected) |
| FORBIDDEN_OBJECT | 引用非授权对象 | 201 (rejected) |
| EXECUTION_ERROR | 执行期数据库错误 | 201 (failed) |
| TIMEOUT | 语句超时 | 201 (failed) |
| VALIDATION_ERROR | 请求体格式无效 | 422 (error) |
| NOT_FOUND | 查询记录不存在 | 404 (error) |

### API 决策

| 决策 | 选项 | 结论 | 理由 |
| --- | --- | --- | --- |
| 查询提交模型 | 同步 vs 异步轮询 | 同步 | 首轮查询有 30s 超时和 1000 行上限，同步足够；避免引入任务队列复杂度。 |
| 拒绝/失败的 HTTP 状态码 | 200/201 vs 4xx | 201 + 业务状态 | 策略拒绝和执行失败是正常业务结果，不是客户端错误；审计记录已创建，资源已产生。 |
| 结果行格式 | 对象数组 vs 列+行数组 | 列定义 + 行数组 | 减少 JSON 体积，前端直接映射表格；列定义包含名称和类型。 |

## 查询工作台

### 功能

- SQL 文本输入区（`<textarea>`，等宽字体）。
- 提交按钮，提交后禁用并显示执行中状态。
- 成功：渲染结果表格（列头 + 数据行）和元信息（行数、耗时）。
- 拒绝/失败：显示错误码和可读说明。
- 不实现：语法高亮、自动补全、多标签、历史记录面板、图表。

### 数据流

```
用户输入 SQL → 点击提交
  → POST /api/v1/query-runs { "sql": "..." }
  → 等待响应（同步）
  → 根据 status 渲染结果表格或错误信息
```

### 前端决策

| 决策 | 选项 | 结论 | 理由 |
| --- | --- | --- | --- |
| 状态管理 | Redux vs Context vs 组件内 | 组件内 useState | 首轮只有一个查询表单，无跨组件共享状态。 |
| HTTP 客户端 | axios vs fetch | fetch | 无拦截器需求，原生 fetch 足够。 |
| UI 框架 | Ant Design vs 无框架 | 无框架，原生 CSS | 首轮只需一个表单和一个表格，不引入组件库依赖。 |
| API 地址配置 | 硬编码 vs 环境变量 | Vite 环境变量 `VITE_API_BASE_URL` | 支持 Compose 端口可配置。 |

## 迁移与幂等 Seed

### 迁移策略

| 数据库 | 工具 | 说明 |
| --- | --- | --- |
| platform | Alembic | 管理 query_runs 表结构演进。 |
| analytics | Alembic（独立配置） | 根据 contract.json 建立五张表结构；不使用 ORM 自动建表。 |

两个数据库使用独立的 Alembic 配置（独立 `alembic.ini` 或独立 `env.py`），迁移脚本分目录存放。

### Seed 流程

1. 使用 analytics_admin 身份连接 analytics 库。
2. 对每张表执行 `TRUNCATE ... CASCADE`。
3. 使用 `COPY FROM` 从 CSV 文件批量导入。
4. 重置序列到 `max(id) + 1`。

幂等保证：TRUNCATE + COPY 是替换式写入，重复执行结果一致。

### 启动顺序

```
PostgreSQL 就绪
  → 创建逻辑数据库（若不存在）
  → 创建角色并授权（若不存在）
  → 运行 platform 迁移
  → 运行 analytics 迁移
  → 运行 seed
  → API 启动（/ready 返回 200）
  → Web 启动
```

## Compose 并行隔离

### 服务

| 服务 | 镜像/构建 | 说明 |
| --- | --- | --- |
| db | postgres:18 | 单容器双逻辑库 |
| api | 本地构建 (Dockerfile) | FastAPI 应用 |
| web | 本地构建 (Dockerfile) | Vite 构建 + 静态服务 |

### 隔离规则

| 约束 | 实现 |
| --- | --- |
| Compose 项目名可配置 | 默认取当前目录名；可通过 `COMPOSE_PROJECT_NAME` 覆盖。 |
| 宿主端口可配置 | `WEB_HOST_PORT`（默认 5173）、`API_HOST_PORT`（默认 8000）通过 `.env` 或环境传入。 |
| 不固定 container_name | 所有服务使用 Compose 自动命名。 |
| 不固定网络/卷名 | 使用 Compose 默认前缀（项目名_网络名）。 |
| 数据库不暴露固定宿主端口 | db 服务不映射端口到宿主；API 通过 Compose 内部网络访问。 |

### 数据库初始化

db 服务使用自定义初始化脚本（挂载到 `/docker-entrypoint-initdb.d/`）：

- 创建 `platform` 和 `analytics` 数据库（`CREATE DATABASE ... ` 幂等检查）。
- 创建 `platform_app`、`analytics_reader`、`analytics_admin` 角色（`DO $$ ... $$` 幂等检查）。
- 授权：platform_app → platform ALL；analytics_reader → analytics SELECT；analytics_admin → analytics ALL。

密码通过 Compose 环境变量传入，不硬编码。

## 测试接缝

### 单元测试（pytest）

| 测试目标 | 切入位置 | 覆盖 |
| --- | --- | --- |
| SQL 策略引擎 | Policy Engine 模块入口 | 允许的查询（SELECT、WITH、JOIN、子查询、窗口函数、UNION）；拒绝的查询（多语句、DML、DDL、数据修改 CTE、SELECT INTO、非授权表、系统目录）；边界语法（注释、引号标识符、大小写）。 |
| LIMIT 注入 | Executor 的 SQL 改写逻辑 | 无 LIMIT 时追加上限；LIMIT 超上限时收紧；LIMIT 在上限内时保持。 |
| 响应序列化 | Router 层 | 各状态的 JSON 结构正确性。 |

### 集成测试（pytest + 真实 PostgreSQL）

| 测试目标 | 切入位置 | 覆盖 |
| --- | --- | --- |
| 双数据库身份隔离 | 直接数据库连接 | platform_app 可写 platform、不可访问 analytics；analytics_reader 可读 analytics、不可写入、不可访问 platform。 |
| 查询运行全链路 | POST /api/v1/query-runs | 成功查询返回正确列和行；拒绝查询返回正确错误码；审计记录已写入 platform。 |
| 迁移幂等性 | 连续运行两次迁移 | 无报错、结构一致。 |
| Seed 幂等性 | 连续运行两次 seed | 行数与 contract.json 一致。 |

### 浏览器测试（Playwright）

| 测试目标 | 切入位置 | 覆盖 |
| --- | --- | --- |
| 查询成功主流程 | 工作台页面 | 输入 SELECT → 提交 → 显示执行中 → 结果表格出现。 |
| 查询拒绝主流程 | 工作台页面 | 输入 DELETE → 提交 → 显示拒绝原因。 |
| 空输入保护 | 工作台页面 | 空 SQL 时提交按钮禁用或显示提示。 |

### 前端单元测试（Vitest）

| 测试目标 | 切入位置 | 覆盖 |
| --- | --- | --- |
| 结果表格渲染 | 表格组件 | 列定义映射为表头；行数据正确渲染。 |
| 错误展示 | 错误信息组件 | 拒绝和失败状态分别展示错误码与说明。 |

### 测试基础设施

- 集成测试和浏览器测试使用独立 Compose 项目名启动测试环境。
- 测试数据库使用与开发相同的迁移和 seed 流程。
- pytest fixture 管理测试数据库生命周期。

## 失败与迁移

### 故障处理

| 场景 | 处理 |
| --- | --- |
| SQL 解析失败 | 拒绝，返回 PARSE_ERROR 和解析器错误位置。 |
| 执行期数据库错误 | 失败，返回 EXECUTION_ERROR 和错误摘要（不暴露内部细节）。 |
| 语句超时 | 失败，返回 TIMEOUT。PostgreSQL `statement_timeout` 强制终止。 |
| 数据库连接不可用 | /ready 返回 503；查询提交返回 503 + 错误信息，不产生审计记录。 |

### 版本迁移路径

- platform 迁移由 Alembic 管理，支持 upgrade/downgrade。
- analytics 表结构由 contract.json 驱动；数据集版本变更时通过新迁移演进。
- seed 数据替换式写入，支持数据集更新后重新填充。

## 延后决策

| 事项 | 原因 |
| --- | --- |
| 异步查询与轮询 | 30s 超时内同步足够；引入队列增加复杂度。 |
| 查询结果分页 | 1000 行上限内无需分页。 |
| 用户认证 | 首轮单用户本地使用。 |
| 查询历史列表 UI | API 已支持按 ID 查询；列表 UI 留待后续。 |
| 连接池调优 | 首轮单用户，默认池足够。 |
| 日志与可观测性 | 首轮依赖标准输出；结构化日志和 tracing 留待后续。 |
| HTTPS / TLS | 本地开发环境，HTTP 足够。 |
