# 06 — 取消请求链路与发布竞态

**What to build:** 用户可以取消排队或运行中的查询：`queued` 直接取消且不会被 Worker 领取；`running` 先持久化取消意图进入 `cancelling`，再 best effort 请求数据库取消。重复取消幂等，不制造新状态或错误。取消与终态发布以条件更新竞争：取消意图先记录则取消获胜、之后返回的结果被丢弃；成功终态与结果已原子提交则迟到取消只返回既有事实。`cancelling` 不会无限停留：所有者失联时由接管/清理循环在租约过期后的一个轮询周期内收敛。依据 CONTEXT.md「取消请求」术语与 spec 取消小节。

**Blocked by:** 04 — 租约、心跳、接管与全局容量

**Status:** ready-for-agent

- [ ] `POST /api/v1/query-runs/{id}/cancel` 按合同返回：`queued` 直接取消并返回 200；`running`/`cancelling` 返回 202；已 `cancelled`/`succeeded` 返回 200 与既有终态；`rejected`/`failed` 返回 409 `query_run_not_cancellable`
- [ ] `queued` 运行取消后进入 `cancelled` 且不能被 Worker 领取
- [ ] `running` 运行取消先持久化取消意图并进入 `cancelling`，再 best effort 请求数据库取消；数据库取消失败不撤销取消意图，之后返回的结果仍被丢弃
- [ ] 取消意图持久化后不得领取新执行尝试；当前所有者在数据库工作结束或取消请求返回后收敛为 `cancelled`
- [ ] 所有者失联时，接管/清理循环在租约过期后的一个轮询周期内把 `cancelling` 收敛为 `cancelled`
- [ ] 重复取消幂等，不制造新状态或错误
- [ ] 两种竞态顺序均有证据：取消意图先于终态发布记录则取消获胜并丢弃结果；成功终态与结果已原子提交则迟到取消返回既有成功事实
- [ ] 取消后的运行不再出现新执行尝试（自动尝试不绕过取消）
- [ ] 取消事实（时间、状态迁移、关联执行尝试）进入审计事实
