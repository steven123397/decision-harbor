# 08 — 重试运行

**What to build:** 用户可以为 `failed` 或 `cancelled` 运行创建重试运行：重试创建记录 `retry_of` 关联的新查询运行并返回 202，新运行拥有独立生命周期与审计事实，进入正常治理与执行链路且可被独立取消。重试幂等键按来源运行 ID 隔离，幂等重放返回同一新运行。`rejected` 必须修改 SQL 后重新提交，`succeeded` 不可重试，其他非允许状态均返回 409 `query_run_not_retryable`。依据 CONTEXT.md「重试运行」术语。

**Blocked by:** 06 — 取消请求链路与发布竞态；07 — 提交幂等键

**Status:** ready-for-agent

- [ ] `POST /api/v1/query-runs/{id}/retry` 对 `failed`/`cancelled` 运行创建记录 `retry_of` 的新查询运行并返回 202
- [ ] 新重试运行进入正常治理与执行链路（策略判定、执行、取消均适用），原始运行的审计事实保持不变
- [ ] 重试幂等：同一来源运行的幂等重放返回同一新运行与 202，不重复创建
- [ ] `rejected` 运行重试返回 409 `query_run_not_retryable`（策略拒绝必须修改后重新提交）
- [ ] `succeeded` 运行重试返回 409 `query_run_not_retryable`
- [ ] 其他非允许状态（含非终态）重试返回 409 `query_run_not_retryable`
- [ ] 重试关联（来源运行、时间）进入审计事实
