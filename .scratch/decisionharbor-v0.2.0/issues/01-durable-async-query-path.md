# 01 — 打通持久异步查询主链

**What to build:** 让分析用户提交受治理 SQL 后立即获得持久查询运行，并由独立 Worker 完成执行。用户可以轮询状态、刷新后按运行标识恢复并读取结果；策略拒绝仍在提交阶段同步形成终态，API 不再执行用户 SQL。

**Blocked by:** None — can start immediately.

**Status:** claimed

- [ ] 合法 SQL 的首次提交同步完成请求校验与策略判定，返回 HTTP 202 和 `queued` 查询运行，不在提交请求内等待 analytics 执行或返回结果。
- [ ] 策略拒绝返回 HTTP 422 和持久化的 `rejected` 查询运行，保留稳定策略事实且不会被 Worker 领取；既有 AST 默认拒绝、对象范围和对象标识类型转换边界保持不变。
- [ ] 独立 Worker 使用 analytics 只读身份领取排队运行，并使其经过 `queued → running → succeeded | failed`；API 只持有 platform 与独立就绪探测凭据，不持有 analytics 查询凭据。
- [ ] 成功终态与可重复读取的结果快照在同一 platform 事务中保存；执行失败形成稳定、脱敏的 `failed` 终态，不泄漏数据库消息、原始 SQL、堆栈、DSN 或凭据。
- [ ] 按标识读取运行时，存在返回 HTTP 200，不存在返回 HTTP 404 `query_run_not_found`；读取结果时，成功返回 HTTP 200，未完成返回 HTTP 409 `result_not_ready`，终态无可读快照返回 HTTP 409 `result_unavailable`，且读取不会重新执行 SQL。
- [ ] 工作台提交后轮询同一查询运行，避免重叠轮询，并在终态、卸载或不可恢复错误后停止；刷新后可以恢复当前运行，并清晰区分排队、运行、成功、策略拒绝和执行失败。
- [ ] 完整 Compose 环境可启动 Web、API、独立 Worker 和 PostgreSQL，公开 HTTP 与浏览器证据证明一次合法查询可从提交走到持久结果，策略拒绝始终未进入 analytics 执行。
