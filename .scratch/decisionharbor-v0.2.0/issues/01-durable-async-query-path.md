# 01 — 打通持久异步查询主链

**What to build:** 让分析用户提交受治理 SQL 后立即获得持久查询运行，并由独立 Worker 完成执行。用户可以轮询状态、刷新后按运行标识恢复并读取结果；策略拒绝仍在提交阶段同步形成终态，API 不再执行用户 SQL。

**Blocked by:** None — can start immediately.

**Status:** resolved

- [x] 合法 SQL 的首次提交同步完成请求校验与策略判定，返回 HTTP 202 和 `queued` 查询运行，不在提交请求内等待 analytics 执行或返回结果。
- [x] 策略拒绝返回 HTTP 422 和持久化的 `rejected` 查询运行，保留稳定策略事实且不会被 Worker 领取；既有 AST 默认拒绝、对象范围和对象标识类型转换边界保持不变。
- [x] 独立 Worker 使用 analytics 只读身份领取排队运行，并使其经过 `queued → running → succeeded | failed`；API 只持有 platform 与独立就绪探测凭据，不持有 analytics 查询凭据。
- [x] 成功终态与可重复读取的结果快照在同一 platform 事务中保存；执行失败形成稳定、脱敏的 `failed` 终态，不泄漏数据库消息、原始 SQL、堆栈、DSN 或凭据。
- [x] 按标识读取运行时，存在返回 HTTP 200，不存在返回 HTTP 404 `query_run_not_found`；读取结果时，成功返回 HTTP 200，未完成返回 HTTP 409 `result_not_ready`，终态无可读快照返回 HTTP 409 `result_unavailable`，且读取不会重新执行 SQL。
- [x] 工作台提交后轮询同一查询运行，避免重叠轮询，并在终态、卸载或不可恢复错误后停止；刷新后可以恢复当前运行，并清晰区分排队、运行、成功、策略拒绝和执行失败。
- [x] 完整 Compose 环境可启动 Web、API、独立 Worker 和 PostgreSQL，公开 HTTP 与浏览器证据证明一次合法查询可从提交走到持久结果，策略拒绝始终未进入 analytics 执行。

## Resolution

Ticket 01 已交付持久异步查询主链。API 只负责持久化与同步策略判定；独立 Worker 使用 PostgreSQL 队列和 analytics 只读身份执行；成功终态与结果快照原子发布；Web 轮询同一运行并在刷新后恢复。外部 Query Run DTO 使用字段白名单，不公开原始 SQL；Worker 仅能更新执行事实列，不能改写查询或策略事实。

### 验收证据

- `python3 datasets/sales-analytics-v1/validate.py`：固定数据集校验通过。
- `COMPOSE_PROJECT_NAME=codex-gpt-56-sol-xhigh-review API_HOST_PORT=28081 WEB_HOST_PORT=25174 ./dev test`：在全新 Compose 项目中通过生产 Web 构建、90 个 API 测试、32 个 Web 测试和 4 个 Playwright 浏览器测试。
- `docker compose config --format json`：API 环境只包含 platform 与 analytics 就绪探测连接，analytics 查询连接只存在于 Worker。
- `git diff --check 9521fb8...HEAD`：通过。

### Review

- 首轮 Standards / Spec 审查分别发现 3 项和 2 项；提交 `68702bb` 收紧 Worker 列级权限、补全 ADR 证据、统一活跃状态判断、移除外部 `raw_sql`，并修复 `received + audit_unavailable` 永久轮询。
- 第二轮基于 `git diff 9521fb8...HEAD` 复核，Standards 为 0 findings，Spec 为 0 findings。

### 提交

- `771a3b6`：持久异步查询主链实现。
- `68702bb`：双轴审查修复。

### 保留风险

- Worker 失联后的租约接管、generation fencing 和全局并发由 Ticket 03 交付；当前版本不会恢复已经领取后失联的 `running` 运行。
- 1 MiB 快照边界、当前所有者发布栅栏与故障注入回滚证据由 Ticket 04 交付；本票只实现现有行数边界下的同事务发布。
- 幂等提交、取消、重试、结果保留和历史仍由后续 tickets 交付。
