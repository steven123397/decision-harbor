---
status: partially-implemented
date: 2026-08-22
---

# 用租约所有权协调恢复、容量与发布

## Context

多个 Worker 必须共享全局容量并在进程失联后接管运行，但失租 Worker 发出的数据库查询不一定已经物理停止。把物理数据库活动数同时当作接管条件和严格容量上限，会使恢复与上限在故障窗口内互相冲突。

## Decision

platform PostgreSQL 协调带租约和递增 generation 的有效执行所有权。全局并发上限约束未过期且 generation 当前的所有权数量；租约过期会释放该所有权并允许新尝试接管。任何状态或结果发布都必须核对当前所有权，旧 generation 永远不能发布。

系统对失租执行尽最大努力请求数据库取消，但不承诺故障窗口内物理 SQL 数量严格不超过所有权上限。自动尝试仅恢复租约丢失或基础设施故障，并受固定尝试上限约束；已记录取消意图的运行不得再次执行。

## Considered options

- 只有确认旧 SQL 物理停止后才允许接管：拒绝。网络分区或 Worker 崩溃时无法可靠取得确认，会让运行永久占用容量。
- 把容量限制放在每个 Worker 进程内：拒绝。增加副本会线性放大 analytics 数据库负载。
- 允许任何尝试发布，依赖最后写入获胜：拒绝。迟到 Worker 可以覆盖接管后的终态或结果。

## Consequences

产品可证明同一时刻最多存在 4 个有效执行所有权和每个运行最多 1 个可发布所有者，但不提供物理 exactly-once 保证。故障注入测试必须分别观测所有权上限和旧 generation 发布失效，不能把数据库会话数当作唯一判据。

## Implementation and evidence

v0.2.0 的 ticket 01 把执行尝试所有权落为 `query_runs` 上的 generation、租约、心跳与尝试序号列，并用 CHECK 约束与 `query_runs_lifecycle_guard` 触发器拒绝终态回退、generation 与尝试序号回退、取消意图被丢弃以及在取消后领取新尝试；`test_query_run_state_model.py` 证明这些约束。

ticket 07 交付数据库协调的全局容量、心跳续租与失租接管。`QueryRunQueue.claim()` 在一条事务内先取事务级 advisory lock，再统计租约未过期的有效执行所有权，只有计数小于 `QUERY_MAX_CONCURRENCY` 时才领取：排队运行与租约已过期的运行共用同一条语句，因此接管同样受这个上限约束，而已经失租的旧活动不计入计数。接管只在 `running` 内进行并保留首次 `started_at`，所以运行不会回退到 `queued`。`worker/leases.py` 的 `LeaseHeartbeat` 按 `WORKER_HEARTBEAT_MS` 续租，每次续租与每次发布都以「执行尝试 + generation + 租约未过期」为栅栏：被接管的旧尝试既续不了租，也发不了状态或结果。`platform_0005` 为租约扫描建立部分索引，使每轮轮询不必读完整条运行历史。`tests/worker/test_lease_capacity.py` 在真实数据库与两个 Worker 副本上证明所有权上限被真正填满且从未突破、心跳在查询执行期间续租、失租被另一副本接管、以及旧 generation 的续租与发布均无效果；`tests/unit/test_worker_leases.py` 与 `tests/unit/test_worker_execution.py` 覆盖续租逻辑与副本自身的容量边界。执行尝试上限与 `cancelling` 的收敛仍属 ticket 08 与 09。

## Revisit when

analytics 执行环境能够提供独立于 Worker 生命周期的强制任务身份与取消确认，或平台需要对物理数据库活动作严格配额时，重新评估所有权释放条件。
