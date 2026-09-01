---
status: implemented
date: 2026-08-16
---

# 在 platform 中保存有限结果快照

## Context

异步查询不能依赖原始 HTTP 响应或浏览器会话交付结果。页面刷新、轮询和历史读取需要稳定结果，但保存无限结果集会使 platform 数据库容量不可预测，重新执行 SQL 又会改变数据时点和审计语义。

## Decision

成功查询在 platform 数据库中与终态原子保存有限、不可变的结果快照。快照保存稳定顺序的结果前缀，受行数、字节数和保留期限制；结果过期后明确不可用，读取接口不重新执行 SQL。

## Considered options

- 只在成功响应中返回结果：拒绝。客户端断开或刷新后无法恢复异步结果。
- 读取时重新执行原始 SQL：拒绝。数据可能变化，也会绕过原运行的资源、取消和审计边界。
- 保存无限完整结果集：拒绝。单次查询可能无界消耗 platform 存储，并增加事务和清理成本。
- 使用对象存储保存结果：本版本不采用。它适合更大结果，但会增加新的依赖和跨存储原子发布问题。

## Consequences

成功终态与快照必须在同一事务中发布，清理只删除结果内容并保留长期审计。用户可能只看到截断前缀，超大单行会失败；产品必须用稳定状态区分未就绪、不可用和已过期结果。

## Implementation and evidence

[有限结果快照 ticket](../../.scratch/decisionharbor-v0.2.0/issues/04-bounded-atomic-results.md) 以紧凑 UTF-8 JSON 精确计量 `columns` 与 `rows`，最多保留 500 行和 1,048,576 字节。Worker 通过命名服务端游标逐行构造最长有序前缀；列定义、首行或任意单行超限时以 `result_too_large` 失败且不保存结果内容。

`apps/api/tests/unit/test_executor.py` 覆盖行数与字节数的精确边界、多字节 UTF-8、累计截断和无部分单元格失败。`apps/api/tests/integration/test_worker_repository.py` 使用真实 PostgreSQL 证明稳定前缀、失败后连接可复用、未释放 execution attempt 的发布栅栏，以及结果插入失败时成功终态和 attempt 释放整体回滚。

Ticket 06 增加了以 platform 数据库 `finished_at + 24h` 和数据库 `now()` 共同判定的结果读取语义。读取、过期清理和并发竞争只操作 platform 快照，不重新执行 analytics SQL；Worker 仅获 `query_run_id` 列读取权与快照 DELETE 权，清理重复执行时保留查询运行、执行尝试和审计事实。真实 PostgreSQL 集成测试覆盖保留期边界、重复与重叠清理、并发读写和权限边界。

## Revisit when

出现大结果导出、长期留存或跨运行结果共享需求时，重新评估对象存储或分层存储；在此之前不扩大 platform 快照上限来替代正式导出能力。
