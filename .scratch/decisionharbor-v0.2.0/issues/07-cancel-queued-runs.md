# 07 — 幂等取消排队中的查询运行

**What to build:** 让分析用户可以可靠撤销尚未开始执行的查询运行。取消后的运行形成稳定终态，即使客户端重复请求或 Worker 同时尝试领取，也不会进入 analytics 执行。

**Blocked by:** 01 — 打通持久异步查询主链

**Status:** resolved

- [x] 取消 `queued` 查询运行以 HTTP 200 返回 `cancelled` 终态，并记录稳定取消时间与审计事实。
- [x] 重复取消已经 `cancelled` 的运行返回 HTTP 200 和同一终态，不创建新状态、新运行或新取消事实。
- [x] 取消与 Worker 领取通过条件写入竞争；取消先成功时运行永远不能被领取，也不能产生新的执行尝试。
- [x] 不存在的运行返回 HTTP 404 `query_run_not_found`；当前不可取消状态返回稳定合同规定的响应，不泄漏内部状态或数据库细节。
- [x] 工作台允许取消排队运行，防止重复操作，并在刷新后继续显示同一 `cancelled` 事实。
- [x] 真实 PostgreSQL 并发证据覆盖取消先获胜、重复取消和 Worker 领取竞争，证明取消后没有 analytics 执行。

## Resolution

Ticket 07 已交付排队查询运行的幂等取消。平台迁移 `platform_0006` 增加 `cancelled` 状态及状态事实约束；API 通过 `POST /api/v1/query-runs/{run_id}/cancel` 在真实 Repository 条件写入下处理 queued 取消、重复取消、迟到终态请求、缺失和不可取消状态。Worker 领取继续使用 `queued` 条件，取消提交后不产生 execution attempt；工作台仅对 queued 运行显示一次性取消操作，并在刷新后恢复同一 cancelled 事实。running/cancelling 取消留给 Ticket 08。

### 验收证据

- `COMPOSE_PROJECT_NAME=decisionharbor-ticket07-final2 API_HOST_PORT=18121 WEB_HOST_PORT=15191 ./dev test`：API 189 passed，Web 37 passed，Playwright 5 passed。
- 真实 PostgreSQL 集成测试覆盖 queued 首次/重复取消、行锁竞争、Worker `SKIP LOCKED` 领取和 `RecordingExecutor.calls == 0`；真实 FastAPI + `TestClient` 路由覆盖 HTTP 200、409 `query_run_not_cancellable`、404 `query_run_not_found`。
- `python3 datasets/sales-analytics-v1/validate.py`、`python3 -m compileall -q apps/api/src apps/api/tests apps/api/migrations/platform/versions` 与 `git diff --check` 均通过。

### Review

- Standards review：0 findings。API、Service、Repository、Domain 分层保持正确；取消合同仅覆盖 queued，未残留 running/cancelling 行为或 schema。
- Spec review：0 findings。真实 HTTP 合同、PostgreSQL 锁竞争、取消后的零执行调用和 queued-only Web 行为均与 Ticket 07 对齐。

### 提交与保留风险

- 实现提交：`efcfbe7`、`84985f0`、`5a5496d`；最终集成证据提交：`9d50c98`。
- running/cancelling 取消及其终态竞争留给 Ticket 08；本票不扩大到重试、历史查询或其他运行状态。
