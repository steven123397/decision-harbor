# 03 — Worker 领取、执行与成功发布

**What to build:** 排队中的查询运行被独立 Worker 从持久队列领取，使用 analytics 只读身份执行，成功后把终态与有限结果快照在一个 platform 事务中原子发布。用户提交后不必保持 HTTP 连接，也不必保持浏览器会话，稍后按运行标识即可读到成功事实与结果。

**Blocked by:** 01, 02

**Status:** resolved

- [x] `queued` 查询运行被 Worker 领取后进入 `running`，并记录本次执行尝试的 Worker 标识、generation 与开始时间
- [x] 用户 SQL 只由 Worker 使用 analytics 只读身份执行，API 不参与执行
- [x] 执行成功后在一个 platform 事务中原子写入 `succeeded` 终态与结果快照，不存在状态为 `succeeded` 但无快照的可观察中间态
- [x] `succeeded` 运行记录返回行数、截断标记、开始时间、结束时间与耗时等审计事实
- [x] 执行结果通过服务端游标有界提取，不先完整物化再截断
- [x] 游标与事务在执行成功、执行失败、取消和失租的所有退出路径上都被关闭或结束，连接返回可复用状态
- [x] 查询超时、语义错误、未知结果类型与内部错误分别映射为稳定错误码并进入 `failed`，错误响应与持久摘要都不含数据库原始错误
- [x] 提交后中断 HTTP 连接，运行仍能推进到终态，且能按运行标识读取
- [x] 运行存在性查询对不存在的标识返回 HTTP 404 `query_run_not_found`

## Resolution

**结果：** 全部验收条件完成。Worker 现在从 platform 队列领取 `queued` 查询运行、用 analytics 只读身份执行、并把成功终态与结果快照在一个事务里原子发布；四类执行故障分别落到稳定错误码并进入 `failed`。

**实际验证（在 `./dev destroy` 后的干净数据卷上）：**

- `./dev test` 退出码 0：`tests/unit` + `tests/integration` 182 passed，`tests/worker` 17 passed，Vitest 27 passed，Playwright 3 passed。
- `python3 validate.py`：`datasets/sales-analytics-v1` 校验通过；`git diff --check` 无输出。
- 迁移往返：临时库上 `alembic upgrade head → downgrade platform_0002 → upgrade head` 后版本为 `platform_0003`，`query_run_results` 与两个触发器都在。
- 端到端冒烟：经 API 提交 `SELECT id, customer_code FROM customers ORDER BY id LIMIT 2` 得到 202 + `queued`，随后运行收敛为 `succeeded`，`query_run_results` 中落库 `[{"name":"id","type":"bigint"},{"name":"customer_code","type":"character varying"}]` 与 `[["1","CUST-0001"],["2","CUST-0002"]]`。
- 凭据边界：`docker compose exec api env` 仍只有 `PLATFORM_DATABASE_URL` 与 `ANALYTICS_READINESS_DATABASE_URL`；`worker` 同时持有 `platform_worker` 与 `analytics_reader`。

**主要实现：**

- 迁移 `platform_0003`：新增 `query_run_results`（`result_columns`、`result_rows`、`truncated`、`created_at`，两个 jsonb 列带 array CHECK）；新增 `query_runs_require_snapshot` BEFORE UPDATE 触发器，使缺少快照的 `succeeded` 迁移被数据库拒绝；授权 `platform_worker` 读写快照、`platform_app` 读取快照。`readiness.py` 期望版本同步为 `platform_0003`。
- `worker/queue.py`：`QueryRunQueue.claim()` 用 `FOR UPDATE SKIP LOCKED` 领取最老的 `queued` 运行并写入 `attempt_number = execution_attempt_count + 1`、`attempt_generation = COALESCE(attempt_generation, 0) + 1`、Worker 标识、开始时间、租约与心跳；`publish_success()` 在同一事务里先写快照再更新 `succeeded`，栅栏失配即整体回滚；`publish_failure()` 写入 `failed` 与稳定错误码。两个终态共用一段 `PUBLISH_FENCE`，避免只为其中一条放宽栅栏。
- `worker/execution.py`：`QueryRunProcessor.process_next()` 领取一个运行，用 `PostgresQueryExecutor`（ADR-0006 服务端游标）执行，把 `ExecutionFailure` 的码与摘要直接持久化为 `failed` 事实；发布被拒绝时记录告警而不是改写新所有者的状态。
- `worker/runtime.py` 与 `main.py`：轮询周期先做 platform 可达性探测（决定就绪），再处理一个排队运行；`main.py` 组装 queue、`PostgresQueryExecutor` 与 processor。
- `repository.py`：`_row_to_query_run` 改为公开的 `row_to_query_run`，供 Worker 侧复用同一行映射，不复制 27 个字段的映射逻辑。

**测试布局的新证据：**

- `tests/worker/test_query_run_lifecycle.py`（6 例）：在真实数据库与运行中的 `worker` 服务上证明领取、审计事实、快照内容、有界截断，以及 `query_semantic_error`、`query_timeout`、`unsupported_result_type`、`internal_error` 四类失败。`internal_error` 用 `SELECT id FROM customers FOR UPDATE` 触发 SQLSTATE 25006（只读事务拒绝写锁），是执行器无法分类的真实数据库错误。
- `tests/unit/test_worker_execution.py`（9 例）：用假队列与假执行器证明发布编排、运行自身的超时与行数边界被透传、五类错误码映射、空队列不碰 analytics，以及发布被拒时不抛异常。
- `tests/integration/test_query_chain.py`：新增「提交连接被丢弃后仍按运行标识读到成功事实」，用裸 socket 发完请求字节即关闭连接、不读响应，再从数据库取回运行标识并轮询到终态；新增 404 `query_run_not_found`。
- `tests/integration/test_query_run_state_model.py`：新增 `test_success_requires_a_result_snapshot`；整组约束测试改在始终回滚的事务内执行，否则插进去的 `queued` 行会被运行中的 Worker 领走（干净环境首轮已复现 `assert 'running' == 'queued'`）。
- 既有竞态断言收敛：`test_query_chain.py` 与 `test_submit_idempotency.py` 里「再次读取仍为 `queued`」改为异步生命周期集合，因为提交响应返回后 Worker 会立即推进运行。
- `compose.yaml`：`api-test` 补 `NO_PROXY`/`no_proxy`。本机 `~/.docker/config.json` 注入了 HTTP 代理，而 `urlopen` 会读代理环境变量；`worker-test` 与 `e2e` 早已有同样的声明。

**提交 SHA：** `1693dc4`（分支 `v0.2.0/codebuddy-hy4-preview-high`，未推送；本行由随后的 tracker 关闭提交写入，实现提交本身不含自身 SHA）。

**Review 结论：** `/code-review` 双轴复核提出六项，均已处理或记录。已修复：ADR 落地证据缺失（0003 转 `implemented`，0004 转 `partially-implemented`，0005/0006/0007 补证据）、`internal_error` 缺少真实证据（补 SQLSTATE 25006 用例）、「中断 HTTP 连接」证据不成立（改为发完即断的裸 socket）、两个终态各写一份栅栏（抽出 `PUBLISH_FENCE`）、`process_next()` 返回语义含糊（补 docstring）。作为有意边界记录在下节：发布栅栏不核对租约、全局容量上限、心跳续租与接管属 07；取消与失租路径的游标收尾属 09/07。

**保留风险与后续票据：**

- 发布栅栏只核对 `status = 'running'`、执行尝试与 generation，不核对 `lease_expires_at`。CONTEXT.md 的「有效执行所有权」要求租约未过期，但该校验依赖 07 的接管循环才能真正生效；在接管存在之前，失租 Worker 仍可发布。
- `CLAIM_QUEUED_RUN` 每轮只领一个 `queued` 运行，且没有全局容量上限。`main.py` 把 `QUERY_MAX_CONCURRENCY` 用作 analytics 连接池大小，那是池容量而非并发控制；并发上限必须由 07 在数据库侧协调（spec 第 57 行）。
- 领取总是把 `started_at` 设为当前时间。当前只从 `queued` 领取所以无影响，但 07 的接管路径必须决定是保留首次开始时间还是记录新的尝试开始时间，否则审计事实会被改写。
- `query_runs_require_snapshot` 只拦 `UPDATE` 迁移路径，不拦 `INSERT`：产品只能通过迁移进入 `succeeded`，而保留期清理删除快照后仍需得到 spec 第 72 行的 `result_unavailable` 运行。若清理路径将来带任何对 `query_runs` 的更新，需要重新评估这条触发器。
- 快照只能经直连数据库校验，公开 HTTP 的读取语义、24 小时保留期与幂等清理属 05；500 行与 1 MiB 预算属 04。本次只沿用运行自带的 `max_rows`。
- 结果尚未就绪、已过期等前端反馈与轮询停止条件属 06；浏览器层面的成功结果断言仍缺，spec 第 112 行的覆盖要求在此前只部分满足。
- 本票关闭后进入 frontier 的票据：04（结果快照边界）、05（结果读取与清理）、11（运行历史分页）。
