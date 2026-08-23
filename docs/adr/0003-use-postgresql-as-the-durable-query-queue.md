---
status: implemented
date: 2026-08-16
---

# 使用 PostgreSQL 持久队列和独立 Worker

## Context

同步 HTTP 执行无法在请求断开或进程重启后继续查询。v0.2.0 需要可恢复的异步生命周期，同时保持只依赖 Docker Compose 的本地运行边界，并让任务状态与产品审计形成一致事实。

## Decision

异步查询运行使用 platform PostgreSQL 记录作为持久队列，由独立 Worker 领取和执行任务。API 同步完成 SQL 策略判定和入队，但不执行用户 SQL。执行所有权、故障恢复和发布栅栏由独立 ADR 约束。

## Considered options

- 引入 Redis 或专用消息队列：暂不采用。它可以提供成熟的投递机制，但会增加本地依赖，并把任务状态与产品审计拆到不同持久边界。
- 在 API 进程内使用后台任务：拒绝。进程重启会丢失所有权，多个 API 副本也无法可靠协调全局容量。
- 保持同步 HTTP 执行：拒绝。它不能满足取消、轮询、接管和结果恢复场景。

## Consequences

任务状态与审计可以共享 PostgreSQL 事务和备份边界，本地环境无需新增基础设施。数据库同时承担队列轮询负载；Worker 的扩展能力受 platform PostgreSQL 的事务和协调能力约束。

## Implementation and evidence

[异步查询主链 ticket](../../.scratch/decisionharbor-v0.2.0/issues/01-durable-async-query-path.md) 由提交 `771a3b6` 落地：`received → queued` 提交合同、基于 `FOR UPDATE SKIP LOCKED` 的独立 Worker 领取、持久结果读取和完整 Compose 链路。API 与 Worker 使用分离的最小权限身份；`apps/api/tests/integration/test_query_chain.py`、`apps/api/tests/integration/test_worker_repository.py` 和 `apps/web/e2e/workbench.spec.ts` 覆盖合法查询成功、策略同步拒绝、执行失败、结果快照读取与刷新恢复。

## Revisit when

队列吞吐、调度能力或跨区域恢复需求超过 platform PostgreSQL 的合理负担，或需要与外部任务生态集成时，基于实测数据重新评估专用消息系统。
