# 首轮实现计划

## 1. 目标与完成定义

本阶段把 [首轮受治理 SQL 查询链路设计](../design/first-release-system-design.md) 实现为可在干净 WSL 环境运行的 Web、API 和 PostgreSQL 基座。

完成必须同时具备：

- `./dev up` 构建并启动 Web、API、单个 PostgreSQL 18 容器及一次性初始化服务。
- 两个逻辑数据库、受限运行时身份、独立 Alembic 迁移和固定数据幂等 seed 可重复执行。
- SQLGlot AST、对象范围和函数允许列表治理只读查询，分析数据库身份提供独立只读约束。
- 查询运行状态、审计事实、稳定错误码和 4 个最小 HTTP 端点可观察。
- 查询工作台展示运行中、成功、拒绝、失败和截断状态。
- pytest、Vitest、真实 PostgreSQL 集成测试和 Playwright 主链均有新鲜证据。
- `git diff --check` 与固定数据集校验通过，状态文档反映实际实现和残余风险。

## 2. 范围与停止线

### 本阶段包含

- Python 3.13、FastAPI、Pydantic、SQLAlchemy 2.0、Alembic、psycopg 3 和 SQLGlot API。
- Node.js 24、React 19、TypeScript、Vite、Vitest 和 Playwright Web。
- PostgreSQL 18 双数据库、运行时身份、迁移、授权、seed 与就绪检查。
- Docker Compose、工作树隔离配置和宿主统一入口。

### 本阶段不包含

- 鉴权、RBAC、租户、生产部署、TLS 或公网安全。
- 异步 worker、队列、查询取消、自动重试、结果持久化或历史列表。
- 自然语言转 SQL、LLM、RAG、MCP、A2A、图表或运营后台。
- 改写背景、数据 contract、公开 CSV、生成器、校验器或 manifest。

### 尚不可规划或待用户决策

无。设计中的延后决策保持停止线，不进入本阶段。

## 3. 前置条件与证据

- 当前 HEAD：`1fb48f499d67677a47fb9b60e1f99346b46e0aee`。
- Docker 29.1.3、Docker Compose 2.40.3 和 Node.js 24.16.0 可用。
- 宿主 Python 为 3.10.12，不作为固定 Python 3.13 栈的验证环境；API 和 pytest 在容器内运行。
- 仓库没有 `.codegraph/`，直接按项目规则读取文件。
- 当前没有应用源码或活跃实现计划。

## 4. 任务清单

### 任务 1：容器化项目基座

**目标：** 建立可解析的 API/Web 依赖清单、容器镜像、Compose 服务和 `./dev` 统一入口。

**依赖与并行：** 无；其余任务依赖本任务提供的测试环境。

**范围：** 新增 `apps/api/`、`apps/web/`、Compose、环境示例和宿主入口；不实现业务行为。

**实现边界：** 固定 Python 3.13、Node.js 24 和 PostgreSQL 18；无 `container_name`、全局网络/卷名或共享绑定目录。

**测试策略：** 配置文件属于 TDD 例外；使用 `docker compose config`、镜像构建和最小进程测试证明机械合同。

**验收证据：** Compose 配置可解析，项目名与 Web/API 端口可覆盖，数据库无宿主端口。

**文档同步：** README 只在真实命令可运行后更新。

### 任务 2：SQL 策略与查询运行编排

**目标：** 纯策略模块和服务编排能稳定区分允许、拒绝、成功与失败，且拒绝查询不触达执行器。

**依赖与并行：** 依赖任务 1 的 API 测试容器。

**范围：** SQLGlot AST、对象、函数与类型转换范围、状态模型、服务错误和结果序列化；不连接真实数据库。

**实现边界：** `SqlPolicy` 纯输入/输出；`QueryRunService` 只依赖 repository/executor 公共接缝；测试替身保留真实合同，不断言 mock 自身行为。

**测试策略：** 先写最窄 pytest 并验证因模块或行为缺失而失败，再写最少实现；依次覆盖允许 SELECT、安全类型转换、多语句、修改语句、对象、对象标识类型、CTE、函数、状态迁移、拒绝不执行和结果截断。

**验收证据：** API 单元测试容器中相关 pytest 全绿，红灯原因有记录。

**文档同步：** 实现发现设计缺口时先停下更新 design；否则不复制设计。

### 任务 3：双数据库迁移、身份与幂等 seed

**目标：** 空 PostgreSQL 18 实例可创建 `platform`/`analytics`、运行时身份、两套 schema 和固定数据；重复初始化无破坏性变化。

**依赖与并行：** 依赖任务 1；为任务 4 的真实 API 提供前置数据。

**范围：** 两套 Alembic 环境、平台审计迁移、分析 contract 迁移、引导程序、查询与就绪身份的最小权限和 seed。

**实现边界：** 公开 CSV 是唯一数据源；seed 使用 advisory lock、hash/行数检查和同事务标记；冲突失败，不自动清空。

**测试策略：** 先写真实 PostgreSQL 集成测试，验证空数据库缺少 schema/权限/数据的红灯；实现后重复运行初始化并验证 contract、计数、查询身份不能读取维护元数据、就绪身份最小可读和跨库拒绝。

**验收证据：** PostgreSQL 18 上迁移 head、5 张表计数、seed 标记、第二次无操作和身份隔离均通过。

**文档同步：** README 记录已证实的启动与重置边界。

### 任务 4：HTTP API 与真实查询链路

**目标：** 4 个端点通过统一 envelope 暴露进程、就绪、查询提交和审计读取。

**依赖与并行：** 依赖任务 2 和任务 3。

**范围：** FastAPI 路由、请求限制、错误映射、平台 repository、分析 executor、应用生命周期和 OpenAPI。

**实现边界：** 平台、分析查询与分析就绪连接严格隔离；同步执行；先审计后执行；终态审计失败不返回结果；`/ready` 依赖探测必须有截止时间。

**测试策略：** 先写 HTTP/真实数据库失败测试，再实现端点；覆盖 health、受限时间内的 ready、成功、拒绝、语义失败、超时、截断和 GET 读取。

**验收证据：** curl/httpx 能取得允许和拒绝查询证据，平台记录与响应一致，拒绝未执行分析 SQL。

**文档同步：** README 记录 API 调用与真实运行命令。

### 任务 5：最小查询工作台

**目标：** 浏览器首屏完成显式 SQL 提交并展示运行中、结果、拒绝、失败和截断。

**依赖与并行：** 依赖任务 4 的稳定 HTTP 合同。

**范围：** React 单页工作台、API 客户端、结果表格、状态/错误视图、全部已知错误码的展示映射和响应式样式。

**实现边界：** 安静、工作导向的分析界面；客户端不复制 SQL 策略；无营销页、历史、图表或隐式重试。

**测试策略：** 先写 Vitest/Testing Library 红灯，覆盖真实组件行为和完整 API 结构；实现后运行组件测试。随后先写 Playwright 主链，再连接完整 Compose 环境使其通过。

**验收证据：** Vitest 全绿；Playwright 观察允许结果、拒绝原因、运行状态和查询运行 ID；桌面/移动截图无重叠。

**文档同步：** 不把视觉说明写入产品状态；只记录已完成主链。

### 任务 6：统一验收与状态同步

**目标：** 用公开入口证明项目满足首轮完成定义，并诚实记录未验证范围。

**依赖与并行：** 依赖任务 1 至任务 5。

**范围：** 全量测试、Compose 重启、健康/就绪/API/浏览器证据、并行配置检查、README/index/plan/status 同步。

**实现边界：** 不自动 commit、push 或扩大范围；不把测试通过当成背景变更。

**测试策略：** 重新运行完整命令，不复用中间结果；验证失败先定位根因并补最窄回归测试。

**验收证据：** `git diff --check`、`python3 validate.py`、`./dev test`、`./dev up`、health、ready、允许/拒绝 API、Playwright 和最终 Git 状态。

**文档同步：** 更新 README、本文执行状态和 `docs/status/project_status.md`；`docs/index.md` 保持完整导航。

## 5. 验证矩阵

| 合同 | 机械证据 | 行为证据 | 共识证据 |
| --- | --- | --- | --- |
| AST 与对象治理 | pytest、固定 SQLGlot 依赖 | 允许/拒绝 API | 与 design 允许集逐项核对 |
| 审计状态机 | schema 检查、pytest | POST/GET 与平台记录 | 术语与 design 一致 |
| 双数据库身份 | Alembic head、权限查询 | 真实 PostgreSQL 写入/跨库拒绝 | 不降低背景安全边界 |
| 固定数据 | contract/hash/行数 | seed 两次、查询业务表 | 不改写产品输入 |
| HTTP API | OpenAPI、pytest | health/ready/成功/拒绝 | 错误语义符合 design |
| 查询工作台 | TypeScript、Vitest | Playwright 主链和截图 | 不扩大前端范围 |
| Compose 隔离 | `docker compose config` | 可配置项目名/端口实例 | 不使用全局资源名 |

## 6. 文档同步与交付边界

- 本计划是本阶段唯一执行事实源，不导出 Ticket 或平行计划。
- 每完成一个任务，立即更新其执行结果；不把进度写入 `AGENTS.md`。
- 完成后 `docs/status/project_status.md` 只保留当前事实、风险和下一步。
- 未经用户明确要求，不 commit、push、创建新 worktree 或清理现有未提交文档。

## 7. 执行结果

更新日期：2026-07-27

- 任务 1 至任务 6 已完成；实现未改写背景资料或固定数据输入。
- `./dev up` 已在 PostgreSQL 18、Python 3.13 和 Node.js 24 容器栈中完成构建、迁移、seed 与健康等待。
- 同一固定数据卷上重复初始化返回 `dataset unchanged`；两个不同 Compose 项目同时就绪且数据隔离。
- 2026-07-27 已补齐 CAST/DataType 保守允许模型，拒绝 PostgreSQL 对象标识类型；允许的审计对象只可能是契约业务表。
- 2026-07-27 已将 seed 标记与迁移版本读取移至独立分析就绪身份，分析查询身份不再读取维护元数据；`/ready` 具有连接、语句和 API 截止时间。
- `./dev test` 在 Compose 内通过 pytest 87 个、Vitest 26 个和 Playwright 4 个测试。
- HTTP 实证覆盖 `/health`、`/ready`、允许查询 `200/succeeded`、策略拒绝 `422/rejected`、语义错误 `400/failed` 与 GET 审计读取。
- SQL 策略、状态机、容量、超时、截断、启动恢复、contract 逐列校验、只读与跨库拒绝均有自动化测试。
- npm audit 报告 0 个已知漏洞；桌面与 390×844 移动视口已在 Chromium 中检查。
