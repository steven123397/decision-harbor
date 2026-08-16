# 架构

## 范围

本设计定义首轮实现的总体结构：组件划分、数据流、运行拓扑与并行工作区隔离。SQL 治理规则见 [query-governance.md](query-governance.md)，数据库身份与 seed 见 [data-and-seeding.md](data-and-seeding.md)，HTTP 语义见 [api.md](api.md)，验证接缝见 [testing.md](testing.md)。产品目标与非目标以 [../background/product-requirements.md](../background/product-requirements.md) 为权威，本文不重复。

## 术语与场景

- 查询运行（query run）：一次 `POST /api/v1/query-runs` 提交的完整生命周期，从收到 SQL 到落入终止状态。
- 只读执行器：使用 `analytics_readonly` 身份在 `analytics` 数据库上执行通过策略检查的 SQL 的 API 内部模块。
- 引导（bootstrap）：容器启动时创建数据库、角色、迁移与固定数据的过程。

代表场景：业务人员在查询工作台输入一条 `SELECT`，前端提交到 API；API 落审计记录、做策略判定、用只读身份执行，并把结果表格或拒绝原因返回给前端。

## 不变量

- 用户 SQL 只在 `analytics` 数据库上以 `analytics_readonly` 身份执行，任何路径不得改用其他身份或数据库。
- 应用写入（查询审计）只发生在 `platform` 数据库，且只使用 `platform_app` 身份。
- 运行中的服务进程不持有超级用户身份；引导身份仅在启动阶段的建库、建角色、迁移和 seed 中使用。
- 结果行数据不持久化；只有审计事实落库。

## 模块边界

```text
/
├─ Makefile          # make up / make test 统一入口
├─ .env.example      # 本地配置模板（.env 已被 .gitignore 忽略）
├─ web/            # React 19 + TypeScript + Vite 查询工作台
│  └─ Dockerfile   # 多阶段：Node 24 构建 → 测试阶段（test profile）→ Nginx 托管静态资源并反向代理 API
├─ api/
│  ├─ Dockerfile   # Python 3.13 + FastAPI（构建上下文为仓库根，数据集烤进镜像）
│  ├─ migrations/  # Alembic，仅管理 platform 库
│  └─ app/
│     ├─ main.py        # 组装、生命周期、/health 与 /ready
│     ├─ bootstrap.py   # 一次性初始化入口（init 容器）：迁移 + seed 后退出
│     ├─ config.py      # 环境变量配置（带上界校验）
│     ├─ db.py          # SQLAlchemy 2.0 双数据库引擎与连接管理
│     ├─ policy/        # SQL 策略判定，纯函数，不访问数据库
│     ├─ runs/          # 查询运行记录仓库与服务编排
│     ├─ execute/       # 只读执行器与服务端游标流式取数、有界连接池
│     ├─ routes/        # HTTP 端点与请求/响应模型
│     └─ seed/          # 由 contract.json 派生 DDL（含 CHECK 约束）并加载固定数据
├─ deploy/
│  ├─ compose.yaml      # Web、API、init、PostgreSQL 与 test profile 测试服务
│  ├─ Dockerfile.db     # postgres:18 + initdb 脚本烤进镜像
│  └─ initdb/           # 首次初始化的建库与建角色脚本
└─ datasets/sales-analytics-v1/   # 产品输入，不修改
```

依赖方向：`routes → runs → (policy, execute, db)`；`policy` 不依赖任何数据库模块；`execute` 只依赖只读引擎；`seed` 只依赖引导引擎与数据集文件。

## 数据流

1. 前端提交 `{ "sql": "..." }` 到 `POST /api/v1/query-runs`。
2. `runs` 服务创建审计记录（`state=running`，保存原始 SQL）。
3. `policy` 基于 SQLGlot AST 判定；拒绝则更新记录为 `rejected` 并返回拒绝码。
4. 通过则 `execute` 以只读身份流式执行，应用语句超时与行数上限。
5. 终态事实（状态、行数、耗时、错误摘要）写回审计记录，响应统一返回运行记录加结果或错误。
6. 前端根据响应展示结果表格、拒绝原因或失败信息；`GET /api/v1/query-runs/{id}` 随后可读取审计事实。

## 并行工作区隔离

多个本地工作区必须可以并行运行，隔离规则如下：

- Compose 项目名从 `.env` 读取（`COMPOSE_PROJECT_NAME`，默认 `decision-harbor`）；网络、数据卷和容器默认以项目名为前缀生成，因此不设置 `container_name`、自定义网络名或数据卷名即可获得天然隔离。
- Web 与 API 的宿主端口从 `.env` 读取（`WEB_PORT` 默认 `8080`、`API_PORT` 默认 `8081`）；两个端口都可配置，并行工作区各用一组端口。
- PostgreSQL 不发布宿主端口，只在 compose 网络内可达；测试与集成经 API 或网络内连接访问。
- `.env` 不入库（`.gitignore` 已覆盖），`.env.example` 提交默认值。
- 源码构建进镜像、不使用绑定挂载（见关键决策），并行工作区不共享任何主机目录。

## 关键决策

- **前端经同源反向代理访问 API，不配置 CORS。** Web 容器内的 Nginx 托管静态资源并把 `/api/`、`/health`、`/ready` 代理到 API 容器。理由：浏览器与测试只面对单一来源，避免本地跨域配置；同时贴近生产形态。
- **源码构建进镜像，不使用绑定挂载。** 两个工作区即使共享内核也不会共享任何主机目录。理由：技术约束禁止共享绑定目录，镜像化是最强的隔离方式，且满足「干净 WSL 一条命令」的目标；代价是本地迭代需要重建镜像，首轮接受。
- **初始化由独立 init 容器执行，服务进程不接触高权限凭据。** compose 拓扑：`init` 服务用 `platform_owner` / `analytics_owner` 身份完成 Alembic 迁移与幂等 seed 后退出；`api` 服务以 `service_completed_successfully` 依赖 init，环境里自始至终只有 `platform_app` 与 `analytics_readonly` 两条低权限连接串。理由：迁移/seed 每次启动都收敛到确定状态，同时保证「运行中的服务进程不持有高权限身份」在容器层面成立，而不是依赖同一容器内的启动顺序。该身份是本地开发凭据，数据库也不发布宿主端口，风险局限在本地 compose 网络内。
- **`make up` 使用 `docker compose up --build --wait` 完成构建、启动与健康等待**，符合「一条统一命令」要求；`--wait` 依赖各容器 healthcheck（API 用 `/health`，就绪业务检查用 `/ready`），init 一次性服务成功退出即视为完成。
- **测试运行在专门构建的测试镜像里，不借用生产容器。** `web-test`（Node 工具链阶段）跑 Web 单元测试，`api-test`（API 镜像 + owner 环境变量）跑双数据库集成测试；两者都在 compose `test` profile 下，`make test` 经 `docker compose run --rm` 一次性执行。

## 失败与迁移

- 执行中途进程崩溃会留下 `running` 状态的记录，属已知边界；首轮不做清理任务，GET 仍可读取。
- Alembic 迁移失败则容器启动失败，compose 不会把流量交给未就绪实例。
- seed 单事务失败即整体回滚，`analytics` 保持上一次成功加载的状态。

## 延后决策

- 查询运行的清理与归档策略。
- API 水平扩展时的连接池上限调优。
- 前端开发态热重载工作流（需要时再引入 dev compose 覆盖文件）。
