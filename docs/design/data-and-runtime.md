# 数据与运行环境设计

## 范围

双数据库与身份边界、迁移与幂等 seed、Compose 并行隔离与启动链路。数据库与隔离的产品约束以 [产品需求](../background/product-requirements.md)、[技术约束](../background/technical-constraints.md) 为准。

## 双数据库与身份边界

PostgreSQL 18 单容器承载两个逻辑数据库：`platform`（查询审计等平台状态）与 `analytics`（固定销售分析数据）。四个身份：

| 身份 | 用途 | 权限 |
| --- | --- | --- |
| 引导超级用户 | 容器初始化脚本建库建角色 | 仅初始化使用，应用运行路径不出现。 |
| `platform_app` | API 与平台迁移 | `platform` 库对象 owner；对 `analytics` 无任何权限（含 CONNECT 被撤销）。 |
| `analytics_owner` | 分析库迁移与 seed | `analytics` 库对象 owner；仅 `migrate` 服务持有其凭据，**API 运行进程不持有**。 |
| `analytics_readonly` | 查询执行器专用 | 仅 `CONNECT analytics`、schema `USAGE`、对五张契约表逐一授予的 `SELECT`；角色默认只读事务；无 `platform` 权限，无任何写与 DDL。 |

理由（安全相关）：数据库权限是应用层策略之外的第二道边界（背景要求）。只读防线分三层：角色默认只读事务（`default_transaction_read_only = on`，初始化时设置）、执行器每条查询 `SET TRANSACTION READ ONLY`（事务级）、逐表最小授权（只授契约五表）。执行身份与 owner 分离，使 SQL 执行路径连 DDL 与 seed 能力都不具备；两个应用身份互不可达对方数据库，任何单点凭证泄露都不跨越数据边界。显式撤销默认授权与全表授权：未来新增的 `analytics` 表不会自动对查询身份可见，需要可见时必须显式授权并同步契约。

初始化：建库、建角色、授权脚本放入 `docker-entrypoint-initdb.d`，仅数据卷首次初始化时执行；脚本按幂等写法（存在则跳过）。

## 迁移与幂等 seed

- Alembic 双迁移上下文：`platform` 建 `query_runs` 审计表（结构见 [查询治理设计](query-governance.md)）；`analytics` 按 `contract.json` 建五表（a0001）并叠加契约业务约束（a0002：`currency` 为 `char(3)` 且仅 `CNY`、订单状态/客户区域/细分的 `allowed_values`、`discount_rate` 范围 0..1、成本不高于标价）——表名、字段、类型、主外键、唯一约束（含 `order_items (order_id, product_id)`）严格对齐契约，不改名不重解释。
- seed：单个事务内按依赖序 `TRUNCATE` 五表，再用 psycopg `COPY` 依序装载 `customers` → `product_categories` → `products` → `orders` → `order_items`；装载后对照契约 `expected_counts` 校验行数，不符则回滚并非零退出。
- 幂等决策：采用截断重载而非 upsert。理由：`analytics` 数据完全由 seed 拥有的固定 fixture（只读身份无写入路径），截重载保证任意次重复执行结果一致且无残留；upsert 会遗留 fixture 收缩后的陈旧行。重复启动时迁移 at head 为 no-op，seed 重载相同数据，满足"重复启动、迁移和 seed 不产生破坏性重复"。

## Compose 并行隔离

- 服务：`db`（postgres:18）、`api`（FastAPI/uvicorn）、`web`（nginx 静态托管 + 反代）。
- 可配置项经环境变量注入（`.env`，由 `.env.example` 提供模板）：Compose 项目名、Web 宿主端口、API 宿主端口。
- 禁止项（背景约束）：固定 `container_name`、固定网络名、固定卷名、共享绑定目录；数据卷使用 Compose 默认项目作用域命名。`db` 不发布固定宿主端口，仅在 Compose 网络内可达；宿主侧调试经 `docker compose exec`。
- 启动链路（统一命令的行为契约，命令形态在计划阶段确定）：构建 → 启动 `db` 并等待健康 → 初始化库与角色 → 执行双库迁移与 seed → 启动 `api`、`web` → 等待 `/health` 与 `/ready` 成功。整条链路可重复执行。

## 失败路径

- seed 行数校验失败：事务回滚，启动命令非零退出，`/ready` 保持 `503`。
- 迁移失败：启动链路中止，`/ready` 保持 `503`。
- `db` 未就绪：`/health` 仍 `200`（进程存活），`/ready` `503`，两者语义区分见 [API 设计](api-and-workbench.md)。

## 测试接缝

身份分离与 seed 幂等在集成测试中验证，见 [测试设计](testing.md)。
