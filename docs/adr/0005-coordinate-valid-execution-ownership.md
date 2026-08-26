---
status: accepted
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

## Implementation status

提交 `bb9853f` 交付全局执行所有权基础：`platform_0004` 保存当前 generation、Worker、心跳、租约和执行尝试；PostgreSQL 事务级协调串行化容量判定与领取；续租和终态发布使用 ownership token 栅栏。`apps/api/tests/integration/test_worker_repository.py` 通过一次性 PostgreSQL 数据库和真实 analytics 执行器证明双 Worker 全局容量、单运行唯一当前所有者和未过期租约不可抢占。

过期运行接管、自动尝试上限、错误分类和失租 analytics 活动取消仍属于 [Ticket 05](../../.scratch/decisionharbor-v0.2.0/issues/05-lease-recovery-and-attempts.md)，本阶段不把 ADR 整体状态提前改为 `implemented`。

## Revisit when

analytics 执行环境能够提供独立于 Worker 生命周期的强制任务身份与取消确认，或平台需要对物理数据库活动作严格配额时，重新评估所有权释放条件。
