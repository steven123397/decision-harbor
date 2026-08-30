# 05 — 接管失租运行并限制自动执行尝试

**What to build:** 让 Worker 崩溃、失联或 analytics 临时不可用后，查询运行可以由新所有者恢复，同时阻止旧执行者发布。自动恢复有明确适用范围和次数上限，不会把永久错误变成无限循环。

**Blocked by:** 03 — 协调双 Worker 的全局执行容量；04 — 原子发布精确受限的结果快照

**Status:** resolved

- [x] 租约过期后，其他 Worker 可以为同一查询运行创建更高 generation 的执行尝试；接管期间运行保持 `running`，不回退到 `queued`。
- [x] 旧 generation 对状态、错误或结果的任何迟到发布均不产生效果；系统只保留一个当前可发布所有者。
- [x] 租约丢失和当前所有者遇到 `analytics_unavailable` 时可以自动尝试；Worker 进程失联通过租约丢失进入同一路径。
- [x] `query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type` 和 `internal_error` 直接形成稳定 `failed` 终态，不自动尝试。
- [x] 每个查询运行最多产生 `WORKER_MAX_EXECUTION_ATTEMPTS` 次执行尝试，默认值为 3；非法上限配置使 Worker 非零退出，尝试耗尽后运行稳定失败。
- [x] 自动尝试复用原 SQL 和治理事实，不改变输入、不绕过策略，也不创建新的查询运行；每次尝试和最终结论保留可重建生命周期的审计事实。
- [x] Worker 对失租数据库活动尽最大努力请求取消，但恢复不依赖物理停止确认；测试分别观测有效所有权和发布栅栏，不承诺物理 exactly-once。
- [x] 故障注入在领取后、analytics 执行中和终态发布前终止 Worker，证明接管、尝试上限、错误分类和旧 generation fencing。

## Resolution

Ticket 05 已交付失租接管和有界自动执行尝试。Worker 领取事务现在可以在全局容量内接管租约已过期的 `running` 运行，保持同一查询运行、原 SQL、治理事实和 `started_at`，并递增 `generation`、封存旧执行尝试。`analytics_unavailable` 在仍有额度时释放当前所有权供下一执行尝试使用；永久错误直接失败，失租执行尝试达到上限后稳定收敛为 `execution_attempts_exhausted`。

每次执行携带独立取消信号。续租失败或异常会让当前 Worker 立即视为失租，并请求取消已登记的 analytics 连接；platform 中的有效所有权仍由租约过期判定。执行器也会在建连前和登记后检查信号，关闭失租发生在连接登记前的竞态。恢复只依赖 platform 所有权和发布栅栏，不等待物理取消确认。

### 验收证据

- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket05-codex API_HOST_PORT=18105 WEB_HOST_PORT=15175 ./dev test`：最终通过 156 个 API 测试、34 个 Web 测试和 4 个 Playwright 浏览器测试。
- `apps/api/tests/integration/test_worker_repository.py`：每条所有权测试使用迁移到 Alembic head 的一次性 platform PostgreSQL；覆盖失租接管、尝试耗尽、`analytics_unavailable` 跨 Worker 恢复、旧 `generation` 的状态与结果发布栅栏、真实 analytics 取消，以及在领取后、analytics 执行中和终态发布前终止独立 Worker 子进程。
- `apps/api/tests/unit/test_worker.py` 与 `test_config.py`：覆盖临时和永久错误分类、最后一次额度、续租返回失租、续租异常、连接登记前取消、默认上限 3 及非法配置。
- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket05-codex docker compose run --no-deps --rm -e WORKER_MAX_EXECUTION_ATTEMPTS=0 worker`：Worker 在启动阶段以状态 1 退出，并指明配置必须位于 1 到 16 之间。
- 在 `datasets/sales-analytics-v1/` 中运行 `python3 validate.py`：固定数据集校验通过；`python3 -m compileall -q apps/api/src apps/api/tests`、Web `npm run build` 与 `git diff --check` 通过。仓库未配置 Python 静态类型检查器，因此没有把 `compileall` 描述为类型检查。

### Review

- 首轮 Standards 发现同一查询运行的后续执行尝试被误称为 retry，并指出自由 `reason: str` 的 Primitive Obsession；首轮 Spec 发现连接登记前失租可能让取消落空，以及缺少三个明确故障窗口的真实 Worker 终止证据。
- 修复后接口收窄为 `release_for_next_attempt()`，每次执行使用独立取消信号，并增加真实子进程三阶段故障注入。最终 Standards 与 Spec 均为 0 个 findings。

### 提交

- `a3ebe27`：实现失租接管、尝试上限、错误分类、analytics 取消和稳定 Web 错误反馈。
- `8da006d`：按 review 收窄 Execution Attempt 术语，关闭连接登记前取消竞态，并增加三个 Worker 终止窗口的真实故障注入。
- `53f9000`：统一剩余测试中的 Execution Attempt 术语。

### 保留风险

- 系统保证同一运行最多一个当前可发布所有者，但不承诺故障窗口内 analytics SQL 物理 exactly-once；取消请求失败或 Worker 被强制终止时，恢复仍由租约与 `generation` 栅栏保证正确性。
