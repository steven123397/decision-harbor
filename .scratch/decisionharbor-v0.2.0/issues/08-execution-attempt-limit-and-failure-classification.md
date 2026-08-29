# 08 — 执行尝试上限与故障恢复分类

**What to build:** 基础设施故障最多触发固定次数的自动执行尝试，临时故障可以自愈；语义与资源类故障直接收敛为 `failed` 而不反复重试。Worker 进程失联通过租约丢失进入同一恢复路径。尝试耗尽后查询运行进入稳定的 `failed` 终态，不会无限循环，也不会意外改变用户的 SQL 或越过治理判断。

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] 未产生终态的租约丢失触发自动执行尝试
- [ ] 当前所有者遇到 `analytics_unavailable`（连接或会话中断）时触发自动执行尝试
- [ ] Worker 进程失联通过租约丢失进入同一自动尝试路径
- [ ] 每个查询运行最多 3 次执行尝试
- [ ] `query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type` 和 `internal_error` 直接进入 `failed`，不自动尝试
- [ ] 自动尝试不改变 SQL、不绕过策略判定、不覆盖已记录的取消意图
- [ ] 尝试耗尽后查询运行进入稳定的 `failed` 终态，并保留稳定的错误码与摘要
- [ ] 每次执行尝试都有稳定的 Worker 标识、generation 与时间事实，可重建一次查询的生命周期
