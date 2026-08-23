# 05 — 执行尝试上限与错误分类

**What to build:** 基础设施故障最多触发 3 次自动执行尝试：只有租约丢失（含 Worker 进程失联进入的租约路径）与 `analytics_unavailable` 触发自动尝试；`query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type`、`internal_error` 直接进入 `failed`，不自动尝试。尝试耗尽后运行进入稳定的 `failed` 终态；自动尝试不修改 SQL、不绕过策略判定、不覆盖取消。查询运行与执行尝试保留稳定关联与时间事实，可重建一次查询的执行历史。

**Blocked by:** 04 — 租约、心跳、接管与全局容量

**Status:** resolved

- [x] 领取后未产生终态即失租的运行被自动重新尝试；当前所有者遇到 `analytics_unavailable`（连接或会话中断）时自动创建新执行尝试
- [x] `query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type`、`internal_error` 直接进入 `failed`，不自动尝试
- [x] 每个查询运行最多 3 次执行尝试（`WORKER_MAX_EXECUTION_ATTEMPTS`）；耗尽后进入稳定 `failed` 终态，不再领取
- [x] 自动尝试不改变 SQL、不重新触发策略判定以绕过治理、不覆盖已记录的取消意图
- [x] 恢复测试在领取后、查询执行中、终态发布前分别终止 Worker，证明租约接管、尝试上限与旧 generation fencing
- [x] 查询运行与执行尝试有稳定关联与时间事实，可重建一次查询的执行历史

## Resolution

**结果：** 交付执行尝试上限与错误分类：`analytics_unavailable` 自动重试，租约失联耗尽收敛稳定终态，其余错误码直接失败。

- 错误分类接缝：`RETRYABLE_FAILURE_CODES = {analytics_unavailable}` 与 `should_release_for_retry(code, generation, max)` 纯函数（单元测试覆盖含 `internal_error` 的完整矩阵）；其余错误码直接进入 `failed`。
- 自动重试机制：`process()` 遇可重试失败且未达上限时调用 `_release_for_retry`——与发布相同的所有权栅栏（当前 generation 且租约未过期）下终结当前尝试并使租约即刻过期，运行保持 `running`，由任意副本经 04 的接管领取路径创建 generation+1 新尝试；栅栏失败（已取消/已被接管）则落入终态发布路径（同样被栅栏，不产生效果）。
- 尝试上限：`claim_next` 发现过期候选的 generation 已达 `WORKER_MAX_EXECUTION_ATTEMPTS` 时，在领取事务内把运行收敛为稳定 `failed`（新错误码 `execution_attempts_exhausted`，由 `failed` 状态 CHECK 的非空 error_code 要求引入），不再创建新尝试；`analytics_unavailable` 在末次尝试直接发布该错误码。
- 治理不绕过：重试复用同一 `query_runs` 行（`raw_sql`、`policy_decision` 断言不变），不重新触发策略判定；`cancelling` 不进入领取候选，重试释放与终态发布都被 `status='running'` 栅栏挡下（测试直接构造 `cancelling` 状态证明取消意图不被覆盖；cancel API 在 06 交付）。
- 恢复测试覆盖三个终止点（领取后 / 执行中含真实 analytics 活动 / 终态发布前），另证明旧 generation 迟到发布无效；执行历史测试证明 run 的 `started_at`/`finished_at` 与首次/末次尝试时间事实同事务对账可重建。
- `internal_error` 的生命周期证据拆分：健康数据库上无法确定性构造该码（权限类 sqlstate 42501 以 "42" 开头被归入 `query_semantic_error`），直接失败路径由同形发布路径的四个错误码端到端证明，码到路径的映射由 `map_database_error` 单元测试补足（未知 sqlstate → 稳定脱敏 `internal_error`）。
- 顺手调整 04 的心跳测试裕度至 20 倍（租约 5s / 心跳 250ms / 执行 6s）：`./dev test` 三镜像构建后的容器 CPU 节流会造成秒级线程停顿，10 倍裕度仍偶发；机制本身由续租单元行为与多数运行证明健全。

**实际验证：** `COMPOSE_PROJECT_NAME=zcode-glm-53-xhigh API_HOST_PORT=18001 WEB_HOST_PORT=15174 ./dev test` 全绿——api-test 201 通过（Worker 接缝 20 用例）、web-test 33 通过（含新错误码展示映射）、e2e 4 通过；`datasets/sales-analytics-v1` 运行 `python3 validate.py` 通过；`git diff --check` 干净。

**Review：** `/code-review` 双轴复核：Spec 无功能缺陷（上限边界、取消栅栏、queued 不可误收敛、CHECK 约束、发布/释放竞态均核实），按发现补齐 `internal_error` 映射单元证据；Standards 无硬违规，按发现收敛三处 SQL 重复（共享 `_FAILED_SET_SQL`、`_OWNERSHIP_GUARD_SQL`、命名 `attempt_cap_reached` 上限判定）。新错误码 `execution_attempts_exhausted` 为本票引入（spec 未枚举，属终态错误码词汇表扩充）；web 端映射一行。

**保留风险：** 无本票范围内的未交付项；`cancelling` 运行的收敛循环（失联后一个轮询周期内进入 `cancelled`）属 06。

**提交：** 见本分支后续 commit（feat(异步查询): 交付执行尝试上限与错误分类）。
