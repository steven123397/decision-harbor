# 数据库身份、迁移与 seed

## 范围

`platform` 与 `analytics` 双数据库的角色设计、权限边界、迁移与固定数据加载。运行拓扑见 [architecture.md](architecture.md)，审计记录的 HTTP 语义见 [api.md](api.md)。

## 数据库与角色

首次初始化（PGDATA 为空）时由 `deploy/initdb/` 脚本创建两个数据库与四个角色：

| 角色 | 数据库 | 权限 | 用途 |
| --- | --- | --- | --- |
| `platform_owner` | `platform` | DDL 与 DML | Alembic 迁移 |
| `platform_app` | `platform` | 仅 DML | 服务运行时的审计读写 |
| `analytics_owner` | `analytics` | schema `analytics` 的 DDL 与数据加载 | 契约 DDL 与 seed |
| `analytics_readonly` | `analytics` | `CONNECT` + schema `USAGE` + 仅五张表 `SELECT` | 用户 SQL 执行 |

权限要点：

- `analytics_readonly` 无任何写权限、无 `CREATE`，对象权限只授予契约五张表；未授权对象（含未来新增对象）默认不可见。表清单不在 initdb 脚本中硬编码：建表发生在 API 引导阶段，`analytics_owner` 建表后按契约表清单逐一 `GRANT SELECT`，与 DDL 同源派生。
- 各库撤销 `PUBLIC` 在 schema `public` 上的 `CREATE`。系统目录（`pg_catalog`）的只读访问是 PostgreSQL 固有行为，不构成写风险；用户 SQL 访问系统目录已由策略层禁止。
- 凭据经环境变量注入 compose；数据库不发布宿主端口，凭据只在 compose 网络内有效。本地默认凭据仅是开发便利，不构成外部暴露。
- 运行中的服务进程只持有 `platform_app` 与 `analytics_readonly`；`platform_owner` 与 `analytics_owner` 凭据只注入一次性 `init` 容器（迁移与 seed 完成后退出），API 服务容器的环境里不存在高权限连接串（见 [architecture.md](architecture.md) 引导决策）。
- initdb 脚本与数据集文件分别烤进 `db` 与 `api` 镜像，不使用主机绑定挂载，并行工作区不共享任何主机目录。

## 迁移（platform）

- Alembic 只管理 `platform` 库，建立 `query_runs`（审计记录）与 `dataset_markers`（seed 标记，见下文）。
- `query_runs` 字段：`id`（`BIGINT GENERATED ALWAYS AS IDENTITY` 主键）、`state`（`running` / `succeeded` / `rejected` / `failed`，CHECK 约束）、`sql`（原文，非空）、`rejection_code`、`rejection_message`、`row_count`、`truncated`（默认 `false`）、`duration_ms`、`error_code`、`error_message`、`created_at`、`finished_at`。首轮仅主键索引；查询列表场景出现后再补索引（延后决策）。
- 主键用自增而非 UUID：单实例部署无分布需求，审计记录量级远在 JSON 安全整数范围内。

## seed（analytics）

- **DDL 从契约派生**：读取 `datasets/sales-analytics-v1/contract.json` 生成 `CREATE TABLE IF NOT EXISTS`，字段类型、`NOT NULL`、主键、唯一约束与外键与契约一致；不手写第二份 schema，契约保持单一事实源。契约中的业务口径同时落为 CHECK 约束：列级 `allowed_values` 生成 `IN` 清单，`business_rules.discount_rate_range` 约束折扣列区间，`product_cost_not_above_list_price` 约束成本不高于标价——违反契约口径的数据在数据库层即被拒绝。
- **数据加载**：读取已提交的权威 CSV，经 psycopg 的 `COPY ... FROM STDIN`（CSV 模式，`NULL ''`）流式加载；时间戳为 ISO 8601 UTC 文本，由 PostgreSQL 解析为 `timestamptz`。seed 不调用生成器，不重新生成数据。
- **幂等标记**：数据集标识（dataset + version + seed，取自 `manifest.json`）写入 `platform` 库的 `dataset_markers`。启动时三分支：
  1. `analytics` 中表不存在 → 建 DDL，单事务加载全部数据，提交后写标记；
  2. 标记匹配且五表行数等于 `expected_counts` → 跳过；
  3. 其他不一致 → 启动失败并报明确错误，不自动重载。

### 关键决策与理由

- **标记放 `platform` 而非 `analytics`**：`analytics` 内只保留契约定义的五张表，业务库不携带实现元数据；同时 `analytics_readonly` 无从篡改标记。
- **不一致即失败而非静默重载**：truncate + reload 是破坏性动作，违反「重复 seed 不得产生破坏性变更」的精神边界；显式失败暴露问题（如契约换了版本或数据被改动），处理方式由人决定。
- **单事务加载**：seed 全量在一个事务内完成，标记在提交后写入，崩溃不会留下半加载数据。

## 失败与迁移

- initdb 脚本只在 PGDATA 为空时执行；修改角色或初始化逻辑需 `docker compose down -v` 重建卷。这是本地开发可接受的恢复路径。
- 固定数据升级（契约新版本）：新 version 使标记不匹配，启动失败提示人工介入；自动升级路径留给后续阶段（延后决策）。
