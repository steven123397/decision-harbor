# 05 — 结果读取语义、保留期与幂等清理

**What to build:** 用户读取结果时得到明确可判别的语义，而不是一个笼统的错误：结果尚未产生、终态但没有可读快照、以及结果已过期各自返回不同状态。结果在保留期内可无限次重复读取，过期后明确不再可用；清理可以重复执行，只删除结果内容，不破坏长期审计事实。

**Blocked by:** 03

**Status:** resolved

- [x] 读取未产生结果的查询运行返回 HTTP 409 `result_not_ready`
- [x] 读取处于终态但没有可读快照的查询运行返回 HTTP 409 `result_unavailable`
- [x] 读取超过保留期的结果返回 HTTP 410 `result_expired`
- [x] 保留期为 24 小时，以数据库记录的 `finished_at + 24h` 为准
- [x] 保留期内重复读取返回同一结果快照
- [x] 结果读取永远不重新执行 SQL
- [x] 结果清理可以重复执行，重叠调度或进程重启都不破坏状态
- [x] 清理只删除结果内容，不删除查询运行的长期审计事实
- [x] 读取不存在的查询运行返回 HTTP 404 `query_run_not_found`
- [x] 错误响应沿用统一 envelope，不泄漏数据库原始消息、堆栈、DSN 或凭据

## Resolution

**结果：** 全部 10 条验收条件完成。`GET /api/v1/query-runs/{id}/result` 只回读已保存的快照，并用三种可判别的状态区分「还没结果」「从未有结果」「结果已过期」；保留期由 platform 数据库按 `finished_at + 24h` 判定，读写两侧共用同一条 SQL 表达式；清理由 Worker 以单条幂等 `DELETE` 承担，只删结果内容。

**实际验证（在 `./dev up` 起栈的本地 Compose 环境上，沿用既有 postgres 数据卷，未执行 `./dev destroy`）：**

- `./dev test` 退出码 0：`tests/unit` + `tests/integration` 249 passed，`tests/worker` 32 passed，Vitest 27 passed，Playwright 3 passed。
- `python3 validate.py`：`datasets/sales-analytics-v1` 校验通过；`git diff --check` 无输出。
- 迁移：在临时库 `migration_check` 上 `alembic upgrade head → downgrade platform_0003 → upgrade head` 结束于 `platform_0004`，`information_schema.role_table_grants` 显示 `platform_worker` 持 `query_run_results` 的 DELETE、`platform_app` 为 0 条；临时库已删除。运行中 platform 库 `\dp query_run_results` 为 `platform_worker=ard`、`platform_app=r`。
- 语义（集成，公开 HTTP）：成功运行返回 200 且 `columns`/`rows`/`truncated` 与落库快照逐字一致；直接写入的 `running` 运行返回 409 `result_not_ready`；策略拒绝运行与直接写入的 `failed` 运行都返回 409 `result_unavailable`；把成功运行的 `finished_at` 回拨 25 小时后返回 410 `result_expired` 且 `query_run_id` 与 error 字段集合固定为 `{code, message, query_run_id}`，回拨 23 小时仍返回 200；随机 UUID 返回 404。
- 不重跑 SQL：同一成功运行连读两次响应完全一致，且 `execution_attempt_count` 仍为 1、`started_at`/`finished_at` 未变；`api-test` 容器的既有断言继续证明 API 侧没有 analytics 执行凭据。
- 清理（worker，真实数据库）：25 小时前的快照被删，而 `query_runs` 行的 `raw_sql`、`status`、`finished_at`、`duration_ms`、`returned_row_count`、`result_truncated` 逐字不变；第二次调用（新的 `ResultRetention` 实例，等价于重启后的进程）返回 0；23 小时前的快照保留；两个 cleaner 并发删除同一行的 `sum(deleted) <= 1` 且终态一致；用 `tests/worker` 里复刻 `decisionharbor.worker.main` 装配的真实 `WorkerRuntime` 以 `platform_worker` 身份 `poll_once()`，过期快照同样消失。
- 调度（单元）：首次轮询即触发清理，间隔内不重复触发，清理抛错时 Worker 仍 `ready` 且队列轮询继续，下一次尝试再等一个间隔。

**主要实现：**

- `domain.py`：`RESULT_RETENTION = timedelta(hours=24)`、`TERMINAL_STATUSES`、三个 `result_*` 错误码、`StoredResult`（运行 + 快照 + 数据库判定的 `result_expired`）与纯函数 `result_read_failure(run, *, has_snapshot, expired)`。
- `repository.py`：`SELECT_RUN_WITH_RESULT` 用一次 `LEFT JOIN` 同时取运行、快照和 `COALESCE(finished_at + :retention <= now(), false)`，新增 `get_result()` 与 `snapshot_from_row()`。
- `service.py`：`read_result()` 把三种语义映射为 `ServiceFailure`，异常收敛为 `audit_unavailable`。
- `api.py`：`GET /api/v1/query-runs/{run_id}/result`；`HTTP_STATUS_BY_CODE` 新增 404/409/409/410 四条；抽出 `_failure_response()`，让提交、审计读取和结果读取共用同一个 envelope 构造点。
- 迁移 `platform_0004`：只给 `platform_worker` 授 `query_run_results` 的 DELETE；`readiness.py` 的期望版本同步为 `platform_0004`。
- `worker/retention.py`：`ResultRetention.delete_expired()` 的单条 `DELETE ... USING query_runs`。
- `worker/runtime.py` 与 `worker/config.py`：每轮轮询后按 `WORKER_CLEANUP_INTERVAL_MS`（默认 60_000，上界 86_400_000）触发一次清理，失败只记日志；`.env.example` 与 `compose.yaml` 同步登记。

**Review 结论：** `/code-review` 双轴复核共 10 项，8 项已处理、2 项按有意边界保留。已处理：ADR 证据里的测试文件名写成被重命名前的 `test_result_read.py`（链接失效）；ADR 中「保留期内被清掉快照的 succeeded 运行仍然可读」这句自相矛盾，改写为运行事实可读、结果报 `result_unavailable`；读取用应用时钟、清理用数据库时钟的双判据，改为读写两侧都由 platform 的 `now()` 计算（`SELECT_RUN_WITH_RESULT` 顺带算出 `result_expired`），顺带消除了 `succeeded` 运行 `finished_at` 为 NULL 时的类型错误；`get_query_run` 里手写 503/404 的两处 envelope 统一到 `_failure_response`；`.env.example` 补 `WORKER_CLEANUP_INTERVAL_MS`；`snapshot_from_row` 前的空行；`result_read_outcome` 改名为 `result_read_failure`（它返回的是失败码，不是读取结果）；`retention.py` 补注「只有 succeeded 运行会有快照行」。Spec 轴还补了一条真实 Worker 装配的清理证据，因为原来只有 SQL 层证据。按有意边界保留：清理 SQL 不按 `status = 'succeeded'` 过滤（不变式已在注释中写明，`publish_success` 与触发器保证只有成功运行有快照行；加过滤会让未来的其他快照漏清理）；清理失败只记 warning 不打断队列轮询（维护故障不应伪装成队列故障，也不应停止领取）。

**保留风险与后续票据：**

- 真实 `worker` 服务的 60 秒清理周期只有装配级证据：单元测覆盖调度逻辑，worker 测证明真实 `WorkerRuntime.poll_once()` 会清理；没有等待一个完整间隔让运行中 Worker 服务自己触发一次的端到端证据。
- 保留期的秒级边界未测：集成测试用 23 小时仍可读、25 小时已过期两点证明，SQL 判定是 `finished_at + 86_400 秒 <= now()`。
- 「保留期内的 `succeeded` 运行没有快照行」只有单元证据。真实路径下 succeeded 运行必定有快照，除非已被清理（此时已过保留期，报 410）。
- `query_runs.finished_at` 上没有索引，每 60 秒一次的清理会扫描 `query_runs`；本版数据规模下无影响，未加索引。
- 前端还没展示三种新错误码，也还没调用 `/result`，属 06。
- `WORKER_CLEANUP_INTERVAL_MS` 是 spec 未列出的配置键（第 61 行允许额外记录并验证上界），清理需要一个调度周期，已同步 `.env.example`、`compose.yaml` 与启动日志。

**提交 SHA：** `45acca0`（分支 `v0.2.0/codebuddy-hy4-preview-high`，未推送；本行由随后的 tracker 关闭提交写入，实现提交本身不含自身 SHA）。

**本票关闭后 frontier 为：** 06（工作台轮询与结果，仅剩的 blocker 是刚关闭的 05）、07（租约、容量与接管）、11（运行历史分页）；13 还等 08 与 12。
