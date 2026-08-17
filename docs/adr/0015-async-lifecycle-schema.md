# 运行表即队列：八状态、租约与代内建于 query_runs，快照独立成表

v0.2.0 把查询运行扩展为异步生命周期。队列、所有权与并发协调全部内建在 platform 库的 `query_runs` 上：状态 CHECK 扩为八态（received/queued/running/cancelling/cancelled/succeeded/failed/rejected），执行者认领即写执行租约（worker_id + lease_expires_at）并递增 generation，认领与终态发布走 `FOR UPDATE SKIP LOCKED` + 代 fencing；不引入外部 broker——本地 Compose 的持久性、接管与全局并发语义由数据库一个事实源保证，少一个组件的故障边界。结果快照独立成 `query_run_snapshots`（一运行一行，随成功终态同事务写入），使 24 小时过期清理可以整行删除而不动审计行。

## 与既有 ADR 的关系

- 与 ADR-0008「同步运行语义」不冲突但属其前奏：0008 的同步语义由后继票显式重开，本 ADR 只锁定 schema 结构，不改运行行为（expand 阶段）。
- 不违背 ADR-0013：幂等键唯一索引只约束提交幂等，与 seed 标记无关。

## 被否方案

- 外部队列（Redis / RabbitMQ）：多一个基础设施组件的必要性、故障边界与资源清理无法在本产品规模下自证。
- 快照留在 `query_runs` 行内：过期清理与审计保留期不同步，行体积与 VACUUM 压力耦合。
