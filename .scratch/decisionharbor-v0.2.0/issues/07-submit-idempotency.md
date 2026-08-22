# 07 — 提交幂等键

**What to build:** 分析用户重复发送同一幂等请求时得到同一查询运行：提交接口支持 `Idempotency-Key` 请求头，键为 1 到 128 个可见 ASCII 字符。相同键与完全相同输入返回原运行（允许查询的重放返回 202，策略拒绝的重放返回 422 与原 `rejected` 运行）；相同键与不同 SQL 返回 409 `idempotency_conflict`；未提供键时每次请求创建新运行。当前版本没有用户或租户，键作用域为整个产品实例。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** ready-for-agent

- [ ] 未提供键时每次请求创建新查询运行
- [ ] 相同键与完全相同输入的重放返回原运行：允许查询返回 202，策略拒绝返回 422 与原 `rejected` 运行，均不创建新工作
- [ ] 相同键与不同输入返回 409 `idempotency_conflict`
- [ ] 键非法（空、超过 128 字符、含不可见 ASCII 或非 ASCII 字符）返回 422 `invalid_idempotency_key`
- [ ] 键作用域为整个产品实例；重放不产生重复的查询运行或重复执行
- [ ] 错误响应沿用统一 `{data, error}` envelope 与稳定错误码
