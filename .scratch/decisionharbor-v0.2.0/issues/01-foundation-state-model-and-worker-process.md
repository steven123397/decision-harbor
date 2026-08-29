# 01 — 异步运行地基：状态模型、Worker 进程与凭据边界

**What to build:** 让 platform 数据模型能够承载完整的异步查询生命周期，并让执行用户 SQL 的能力只属于独立 Worker 进程。查询运行获得本版本需要的全部状态、取消意图、执行尝试计数、重试关联和幂等记录；Worker 作为一个独立进程在 Compose 环境中启动，加载并校验自己的租约、心跳、轮询和容量配置；API 只保留 platform 凭据与最小就绪探测凭据，不再持有执行用户 SQL 所需的 analytics 查询凭据。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] platform 数据模型支持 `received`、`rejected`、`queued`、`running`、`succeeded`、`failed`、`cancelling`、`cancelled` 全部状态，且 `rejected`、`succeeded`、`failed`、`cancelled` 为不可逆终态
- [x] platform 数据模型记录每次执行尝试的 Worker 标识、递增 generation、租约时间、心跳时间和尝试序号
- [x] platform 数据模型记录查询运行的取消意图、执行尝试计数、重试来源关联以及提交与重试的幂等键作用域
- [x] 非法状态与事实组合被数据库约束拒绝，不能只靠应用代码约定
- [x] API 运行环境中不存在执行用户 SQL 所需的 analytics 查询凭据，API 只持有 platform 凭据与独立的最小就绪探测凭据
- [x] 移除 analytics 执行凭据后，API 的 `/ready` 仍能在 1 秒截止时间内成功，`/health` 仍只表达进程存活
- [x] Worker 作为独立进程在 Compose 中启动，使用默认配置时保持就绪并报告自身健康
- [x] Worker 在任一配置项非法时以非零退出码启动失败并给出可读原因，包括非正整数、以及心跳不严格小于租约
- [x] Worker 默认配置为并发上限 4、租约 15000ms、心跳 3000ms、轮询 250ms、最大执行尝试 3 次
- [x] 既有 SQL AST 默认拒绝、允许对象范围、analytics 只读权限、固定数据口径与超时行数限制回归全部保持通过

## Resolution

**结果：** 全部验收条件完成。platform 状态模型、独立 Worker 进程和凭据边界已落地，API 不再持有执行用户 SQL 的凭据，提交路径同步完成策略判定后入队并返回 HTTP 202。

**实际验证（在 `./dev destroy` 后的干净数据卷上）：**

- `./dev up --wait`：init 完成 `platform_0002` 迁移，api、worker、web 三个服务全部 healthy。
- `./dev test` 退出码 0：`tests/unit` + `tests/integration` 132 passed，`tests/worker` 10 passed，Vitest 27 passed，Playwright 3 passed。
- `python3 validate.py`：`datasets/sales-analytics-v1` 校验通过。
- 迁移往返：在临时库上 `alembic upgrade head → downgrade platform_0001 → upgrade head` 成功，终态列为 `platform_0002`。
- Worker 非法配置：`WORKER_HEARTBEAT_MS=0`、`WORKER_LEASE_MS=2000`（心跳不小于租约）、`WORKER_MAX_EXECUTION_ATTEMPTS=abc`、`WORKER_POLL_MS=-1`、`QUERY_MAX_CONCURRENCY=0` 五种组合在容器内均以退出码 2 退出并打印配置项名称与原因。
- 运行凭据：`docker compose exec api env` 只有 `PLATFORM_DATABASE_URL` 与 `ANALYTICS_READINESS_DATABASE_URL`；`docker compose exec worker env` 有 `platform_worker` 与 `analytics_reader` 两个身份。
- `git diff --check` 无输出。

**主要实现：**

- 迁移 `platform_0002`：`query_runs` 扩展 8 状态 CHECK 与新的 `query_runs_state_facts`；新增 `cancellation_requested_at`、`execution_attempt_count`、`attempt_number`、`attempt_worker_id`、`attempt_generation`、`lease_expires_at`、`heartbeat_at`、`retry_of`；新增 `query_runs_attempt_ownership`、`query_runs_retry_of` CHECK 与 `query_runs_lifecycle_guard` 触发器；新增 `query_run_idempotency` 表（`scope` 限定为 `submit` 或 `retry:<run id>`，键长 1 到 128）；`platform_worker` 身份获得 `query_runs` 读写授权。
- Worker：`decisionharbor.worker` 包，`WorkerSettings` 默认值与正数/上界/心跳严格小于租约校验，`WorkerRuntime` 轮询并在每轮确认 platform 可达（失败时就绪回落），`WorkerHealthServer` 在 8001 提供 `/health` 与 `/ready`，`python -m decisionharbor.worker` 入口处理 SIGTERM/SIGINT。
- API：`Settings` 不再读取 analytics 执行凭据，`QueryRunService.run` 改为 `submit`（策略允许入队、拒绝落库），提交返回 202 且不再携带结果；移除进程内容量信号量与已不可达的 HTTP 状态映射。
- 测试布局：`tests/worker/` 只运行于 `worker-test` 服务（持有 analytics 只读凭据），承载执行回归与 analytics 契约；`tests/unit` 与 `tests/integration` 只运行于 `api-test`（无 analytics 执行凭据），其中 `test_query_chain.py` 断言该环境变量不存在并验证 `/ready` 在 1 秒内成功。

**Review 结论：** `/code-review` 双轴复核提出 ADR 落地状态未更新、取消意图可被抹除、成功终态不要求执行尝试事实、Worker 就绪不回落等问题，均已修复；`docs/adr/0001`、`0002` 的证据与身份描述已更新，`0003`、`0005` 状态改为 `partially-implemented` 并补充实现证据。

**保留风险与后续票据：**

- 浏览器层面的成功结果、执行失败与截断断言在本次移除，因为 API 已不执行 SQL；由 03（领取执行）与 06（工作台轮询）补回，spec 第 112 行的浏览器覆盖要求在此之前只部分满足。
- 提交幂等读写、领取执行、租约接管、取消传播、结果快照分别属于 02、03、07、09、04 等后续票据；本票只交付数据模型与进程地基。
- `cancelling` 的收敛、`cancelling` 时的失联接管与全局容量上限仍缺实现，数据库层已具备记录与拒绝非法写入的约束。
- 前端在 202 后只展示 `queued` 与运行标识，不轮询；轮询与终态展示属于 06。
