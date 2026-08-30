---
status: partially-implemented
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

v0.2.0 的 ticket 03 建立 `platform_0003` 迁移的 `query_run_results` 表，并在 `QueryRunQueue.publish_success()` 中把快照写入与 `succeeded` 终态放进同一个 platform 事务：先写快照再更新状态，任一步失配即整体回滚，因此不存在“已成功但无快照”的可观察中间态。迁移还加 `query_runs_require_snapshot` 触发器，把这条不变式落到数据库层——它只拦 `UPDATE` 迁移路径，不拦 `INSERT`，所以保留期清理删除快照后仍能得到 spec 要求的 `result_unavailable` 运行。

ticket 04 交付两重边界本身。`domain.ResultSnapshotBuilder` 按 `{"columns":[...],"rows":[...]}` 的紧凑 UTF-8 JSON 计量：最多 500 行，最多 1,048,576 字节，插入行时增量累加字节，越界即停止并记录 `truncated`；列定义本身、首行或任意单行超出字节预算时抛出 `ResultTooLarge`，由 `executor` 映射为 `result_too_large` 的 `failed` 运行，不保存部分内容。`queue.publish_success()` 用同一个 `encode_json` 落库，使计量字节与存储表示一致。证据：`tests/unit/test_result_snapshot.py` 覆盖边界计算，`tests/worker/test_result_snapshot_bounds.py` 在真实 analytics 数据上覆盖 500 行边界、恰好 1 MiB 与超出 1 字节、首行与后续单行过大、多字节 UTF-8 与显式类型，`tests/integration/test_query_chain.py` 用策略允许的 1663 列宽结果证明字节预算在公开提交路径上同样生效。读取语义与保留期仍由 ticket 05 交付。

## Revisit when

出现大结果导出、长期留存或跨运行结果共享需求时，重新评估对象存储或分层存储；在此之前不扩大 platform 快照上限来替代正式导出能力。
