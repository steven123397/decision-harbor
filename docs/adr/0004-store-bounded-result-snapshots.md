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

v0.2.0 的 ticket 03 建立 `platform_0003` 迁移的 `query_run_results` 表，并在 `QueryRunQueue.publish_success()` 中把快照写入与 `succeeded` 终态放进同一个 platform 事务：先写快照再更新状态，任一步失配即整体回滚，因此不存在“已成功但无快照”的可观察中间态。迁移还加 `query_runs_require_snapshot` 触发器，把这条不变式落到数据库层——它只拦 `UPDATE` 迁移路径，不拦 `INSERT`，所以保留期清理删除快照后仍能得到 spec 要求的 `result_unavailable` 运行。

ticket 04 交付两重边界本身。`domain.ResultSnapshotBuilder` 按 `{"columns":[...],"rows":[...]}` 的紧凑 UTF-8 JSON 计量：最多 500 行，最多 1,048,576 字节，插入行时增量累加字节，越界即停止并记录 `truncated`；列定义本身、首行或任意单行超出字节预算时抛出 `ResultTooLarge`，由 `executor` 映射为 `result_too_large` 的 `failed` 运行，不保存部分内容。`queue.publish_success()` 用同一个 `encode_json` 落库，使计量字节与存储表示一致。证据：`tests/unit/test_result_snapshot.py` 覆盖边界计算，`tests/worker/test_result_snapshot_bounds.py` 在真实 analytics 数据上覆盖 500 行边界、恰好 1 MiB 与超出 1 字节、首行与后续单行过大、多字节 UTF-8 与显式类型，`tests/integration/test_query_chain.py` 用策略允许的 1663 列宽结果证明字节预算在公开提交路径上同样生效。

ticket 05 交付读取语义、保留期与幂等清理。`GET /api/v1/query-runs/{id}/result` 只回读已保存的快照，从不执行 SQL：非终态返回 409 `result_not_ready`，从未产生结果的终态返回 409 `result_unavailable`，`finished_at` 超出 24 小时的 `succeeded` 运行返回 410 `result_expired`。只有 `succeeded` 曾经有过结果，因此只有它会过期——25 小时前的 `failed` 运行仍然报告“没有结果”，不会被误报成“已过期”。保留期判据是数据库记录的 `finished_at` 加上 `domain.RESULT_RETENTION`：读写两侧都把它交给 platform 的 `now()` 计算（读取在 `SELECT_RUN_WITH_RESULT` 里顺带算出 `result_expired`，清理在 `DELETE ... USING query_runs` 里用同一表达式），因此读取与随后的清理不会就“是否仍在保留期内”给出不同答案，也不受应用进程时钟影响。清理由 Worker 承担：`platform_0004` 只给 `platform_worker` 授 `query_run_results` 的 DELETE，`platform_app` 仍只有 SELECT，所以 HTTP 层既没有执行 SQL 的身份，也没有删除结果内容的身份。`worker/retention.py` 的 `ResultRetention.delete_expired()` 是一条 `DELETE ... USING query_runs`，按保留期选择而不是按上一次清理留下的状态选择，因此重复执行、重叠调度和进程重启都收敛到同一状态；它只删快照行，`query_runs` 上的原始 SQL、策略判定、状态、时间和结果摘要全部保留。由于 `query_runs_require_snapshot` 只拦 `UPDATE` 不拦 `DELETE`，被清掉快照的 `succeeded` 运行本身继续可读，只是它的结果如实报 `result_unavailable`，超过保留期时同一运行报 `result_expired`。证据：`tests/unit/test_result_read_semantics.py` 覆盖三种语义的判定顺序，`tests/unit/test_service.py` 与 `tests/unit/test_api.py` 覆盖稳定错误码、HTTP 状态和脱敏 envelope，`tests/integration/test_result_read.py` 在公开 HTTP 上证明 200、404、409、410、23 小时仍可读、25 小时已过期与保留期内重复读取返回同一快照，`tests/worker/test_result_cleanup.py` 在真实数据库上证明重复清理、并发清理、审计事实保留，以及真实 Worker 装配在 `platform_worker` 身份下完成清理，`tests/unit/test_worker_runtime.py` 证明清理按间隔触发且失败不打断队列轮询。

ticket 06 交付客户端如何消费这套快照。`apps/web/src/runTracking.ts` 的 `useTrackedRun` 按运行标识轮询 `GET /api/v1/query-runs/{id}`，只在运行进入 `succeeded` 后读一次结果快照；本版本不引入 SSE 或 WebSocket，下一轮轮询只在上一轮请求结束后调度，所以同一运行不会产生重叠请求，而终态、组件卸载以及 `query_run_not_found`／`result_expired`／`result_unavailable` 这些再等也不会变化的失败都会停止轮询。运行标识记在页面地址的 `run` 参数里，刷新后按它恢复同一运行的状态、原始 SQL 与结果，浏览器会话因此不再拥有查询生命周期。三种读取语义在界面上是三种可区分的结局，而不是同一种失败：未就绪继续等待并说明仍在轮询，不可用与已过期各自给出原因和下一步。证据：`apps/web/src/runTracking.test.tsx` 覆盖排队到成功的推进、刷新恢复、等待与过期/不可用反馈以及各类停止条件，`apps/web/e2e/workbench.spec.ts` 在真实 Worker 与浏览器中证明结果表格、刷新后仍读到同一结果，以及 `result_expired` 与 `result_unavailable` 的界面反馈。

## Revisit when

出现大结果导出、长期留存或跨运行结果共享需求时，重新评估对象存储或分层存储；在此之前不扩大 platform 快照上限来替代正式导出能力。
