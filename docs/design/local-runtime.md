# 本地运行环境

## 范围

本文定义 Docker Compose 结构、多工作区并行隔离、数据库初始化、迁移与幂等 seed、统一命令和测试的运行方式。可配置性要求的权威来源是 [技术约束](../background/technical-constraints.md)。

## Compose 结构

三个服务，均无 `container_name`，使用 Compose 默认的项目前缀命名：

| 服务 | 镜像来源 | 说明 |
| --- | --- | --- |
| `db` | `postgres:18` 官方镜像 | 单容器双库；数据存于命名卷（Compose 自动加项目前缀）；健康检查用 `pg_isready` |
| `api` | 本仓库 Dockerfile（Python 3.13） | 依赖 `db` 健康后启动 |
| `web` | 本仓库 Dockerfile（Node.js 24） | Vite dev server，`/api` 代理到 `api` 服务 |

网络使用 Compose 默认项目网络，不声明外部或全局网络。绑定挂载只指向当前工作区自身的源码目录，不使用工作区之外的共享路径——技术约束禁止的是跨实例共享的固定资源，每个工作区挂载各自检出目录不在此列。

数据库端口不发布到宿主机；宿主只暴露 `web` 与 `api` 两个可配置端口。

## 并行隔离配置

配置经 `.env` 文件注入（`.env` 不提交，提交 `.env.example` 作为模板）：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | Compose 项目名，决定容器、网络、卷的命名空间 | 无默认，`.env.example` 提示按工作区取名 |
| `WEB_PORT` | 工作台宿主端口 | `5173` |
| `API_PORT` | API 宿主端口 | `8000` |
| 数据库口令类变量 | 三个应用身份的口令 | `.env.example` 提供本地开发默认值 |
| 治理参数 | 语句超时、行数上限、输入长度上限 | 见 [query-governance.md](query-governance.md) |

不同工作区设置不同的项目名与端口即可并行运行，互不共享任何容器、网络或卷。

## 数据库初始化

`db` 首次在空卷上启动时，经 `docker-entrypoint-initdb.d` 初始化脚本创建：

- `platform`、`analytics` 两个数据库；
- `platform_app`、`analytics_owner`、`analytics_reader` 三个身份及其权限（职责见 [architecture.md](architecture.md)）；
- `analytics_reader` 的角色级设置：`default_transaction_read_only = on` 与兜底 `statement_timeout`。

初始化脚本只在空卷上执行一次，天然幂等；权限授予写成可重复执行的形式，供集成测试环境复用。契约表上的 `SELECT` 授权在 analytics 迁移建表后授予（`analytics_owner` 执行迁移时一并授权，或对 schema 配置默认权限）。

## 迁移与幂等 seed

两套独立的 Alembic 迁移目录，各自维护版本表：

- `platform` 迁移：以 `platform_app` 身份建立 `query_runs` 等审计结构。
- `analytics` 迁移：以 `analytics_owner` 身份按 [`contract.json`](../../datasets/sales-analytics-v1/contract.json) 建立契约 schema 与五张表，字段名、类型、约束与外键忠实映射契约，不改名、不增删业务字段。

分开的理由：两库生命周期、演进节奏和执行身份都不同，合用一套版本历史会把分析库结构与平台结构耦合在同一序列里。

seed 将 `datasets/sales-analytics-v1/data/` 的权威 CSV 装载进 `analytics`，幂等策略为**校验后跳过、不一致则报错**：

1. 五表全空 → 按外键依赖顺序装载全部 CSV。
2. 各表行数与契约 `expected_counts` 全部一致 → 视为已装载，跳过。
3. 其他情况（部分装载、行数不符）→ 报错退出，指引人工显式重置（删卷重建）。

理由：装载目标是固定权威数据，"检测到不一致就静默清空重灌"会掩盖真实问题（迁移错误、意外写入），报错让异常显式暴露；正常路径下重复执行零破坏。数据集内容本身的正确性由数据集自带的 `validate.py` 负责，seed 不重复实现校验。

## 统一命令

入口是仓库根目录的 Bash 脚本（干净 WSL 环境只保证 Git、Docker、Docker Compose 存在，故不依赖 `make`、宿主 Python 或 Node）：

- `./dev.sh up`：构建镜像、启动三服务、执行两套迁移与 seed、轮询 `/ready` 直到就绪或超时。重复执行不产生破坏性重复（迁移与 seed 均幂等）。
- `./dev.sh test`：依次运行策略单元测试与集成测试（`api` 容器内 pytest）、前端组件测试（`web` 容器内 Vitest）、浏览器主流程测试（Playwright 官方镜像容器，访问 `web` 服务）。任一环节失败即整体失败。
- `./dev.sh down`：停止并移除本实例的容器与网络；数据卷默认保留，显式参数才删除。

所有测试与工具均在容器内运行，宿主不需要安装语言运行时。

## 失败与迁移

- `/ready` 等待超时说明迁移或数据库启动失败，脚本以非零退出并提示查看对应服务日志。
- PostgreSQL 大版本升级需要删卷重建（本地数据可由迁移 + seed 完全重建，无保留价值）。

## 测试接缝

- 单元与组件测试不依赖 Compose 运行态，可独立于 `up` 执行。
- 集成与浏览器测试依赖已就绪的实例，`./dev.sh test` 负责先行确保就绪。
- CI 或多工作区并行跑测试时，凭 `COMPOSE_PROJECT_NAME` 与端口配置互不干扰。

## 延后决策

- 生产部署形态（构建产物托管、反向代理、TLS）：首轮仅本地环境，出现部署需求时重议。
