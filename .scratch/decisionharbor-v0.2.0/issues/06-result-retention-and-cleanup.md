# 06 — 过期并幂等清理结果快照

**What to build:** 让分析用户在明确的保留期内重复读取结果，并在结果过期后得到不同于失败或不存在的稳定反馈。平台可以安全重复清理结果内容，同时长期保留查询运行和审计事实。

**Blocked by:** 04 — 原子发布精确受限的结果快照

**Status:** resolved

- [x] 结果保留期以数据库记录的 `finished_at + 24h` 为准；保留期内读取成功快照返回 HTTP 200，边界到达后返回 HTTP 410 `result_expired`。
- [x] 未完成运行返回 HTTP 409 `result_not_ready`，终态但没有可读快照返回 HTTP 409 `result_unavailable`；不存在仍返回 `query_run_not_found`，各种语义互不混淆。
- [x] 清理操作可以重复、重叠或在进程重启后再次执行，只删除已过期结果内容，不删除查询运行、执行尝试、取消、重试关联或其他长期审计事实。
- [x] 清理和结果读取并发时由数据库事实给出稳定结论，不重新执行 SQL，也不返回已经被判定过期的旧内容。
- [x] 工作台对未就绪、不可用和已过期结果给出不同且可操作的反馈，并在不可恢复结果状态下停止轮询。
- [x] 使用可控数据库时间的集成证据覆盖过期边界、重复清理、并发清理及审计事实保留。

## Resolution

Ticket 06 已交付。API 通过 platform 数据库的 `finished_at + 24h` 与数据库 `now()` 判定快照可读性；过期读取返回 `410 result_expired`，未就绪和终态无快照分别返回 `409 result_not_ready` 与 `409 result_unavailable`。Worker 使用可配置的周期清理，只删除过期快照并保留 query run、execution attempt 和审计事实；读取与清理均不重新执行 analytics SQL。Web 工作台区分三类结果状态，并对过期结果提供重新运行的可操作反馈。

### 验收证据

- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket06-codex API_HOST_PORT=18106 WEB_HOST_PORT=15176 ./dev test`：API 179 passed，Web 35 passed，Playwright 4 passed。
- 集成测试使用真实 PostgreSQL 覆盖 `finished_at + 23h/24h` 边界、重复清理、重叠清理、读写并发，以及清理后 run/attempt/audit 事实保留。
- `python3 validate.py`（`datasets/sales-analytics-v1`）、`python3 -m compileall -q apps/api/src apps/api/tests`、`npm run build`（`apps/web`）和 `git diff --check` 均通过。
- `platform_0005` migration 经过 up/down/re-up roundtrip；Worker 仅新增 `query_results.query_run_id` 的 SELECT 与快照 DELETE 权限，API 仍不具备快照 DELETE 权限。

### Review

- Standards review：未发现硬性规范问题；审查指出的三个 P3（保留期 SQL、时间回溯 helper、测试 helper 命名重复）均已修复并重新验证。
- Spec review：人工逐项对照 ticket 与 spec，未发现遗漏或超范围实现。并行 Spec review agent 因服务端连续返回 HTTP 429 未能运行，故以本地证据完成替代复核。

### 提交与保留风险

- 实现提交：`8745fb2fc23f953d6a57e66ed0b0785c95afbc97`。
- 当前清理触发点是 Worker 轮询前的周期任务；下一张 frontier ticket（07）取消队列运行仍未开始。本票不扩大到重试、历史查询或导出保留策略。
