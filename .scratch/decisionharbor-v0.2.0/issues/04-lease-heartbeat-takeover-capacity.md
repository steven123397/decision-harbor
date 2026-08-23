# 04 — 租约、心跳、接管与全局容量

**What to build:** 平台维护者可以运行 2 个 Worker 副本共享数据库协调的全局并发上限（默认 4）：Worker 按心跳间隔续租；进程失联后租约过期，其他副本接管运行并创建 generation+1 的新执行尝试，运行保持 `running` 不回退；旧 generation 永远不能发布状态或结果。依据 ADR-0005（有效执行所有权）。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** resolved

- [x] Worker 持有期间按 `WORKER_HEARTBEAT_MS` 间隔续租；所有权按 `WORKER_LEASE_MS` 过期
- [x] 租约过期后其他 Worker 可接管并创建新执行尝试（generation 递增）；接管期间查询运行保持 `running`，不回退 `queued`
- [x] 旧 generation 的任何状态或结果发布不产生效果；同一运行同一时刻最多一个可发布所有者（当前 generation 且租约未过期）
- [x] 两个 Worker 副本同时运行时，数据库中未过期的有效执行所有权从不超过 4
- [x] 容量判定不把已失租但尚未物理停止的旧数据库活动计为有效所有权
- [x] 双 Worker、租约过期、接管、旧 generation 场景用真实 PostgreSQL 事务与约束构造（基于数据库的 Worker 集成接缝），不以 Mock 代替

## Resolution

**结果：** Worker 领取路径支持租约接管，发布路径补齐租约栅栏，双副本全局容量以数据库协调。

- `claim_next` 候选查询统一为「`queued` 运行 或 当前尝试租约已过期的 `running` 运行」（`FOR UPDATE OF r SKIP LOCKED`，FIFO 按 `created_at, id`）：接管插入 generation+1 尝试、终结已失租旧尝试，运行保持 `running` 且 `started_at` 保留首次事实，不回退 `queued`；`cancelling` 不进入领取候选（取消后不再执行，收敛属 06）。
- 发布栅栏抽取为 `_PUBLISH_FENCE_SQL`：`succeeded`/`failed` 发布都要求「当前 generation 且租约未过期」（`current_attempt_id` 匹配 + `lease_expires_at > now()`），失租后即使尚无人接管也不能发布；被栅栏尝试立即终结不泄漏容量。
- 迁移 `platform_0005`：`query_runs_takeover_idx`（`WHERE status='running'` 部分索引）服务接管扫描；`readiness` 的 platform 迁移头同步推进到 `platform_0005`。
- `run_forever` 支持注入停止事件且主循环真正检查退出（心跳/清理/主轮询三循环一致停止）。
- 新增集成测试（真实 PostgreSQL，无 Mock）：租约未过期不可抢占；过期接管 generation 递增且运行保持 `running`；失租当前尝试不能发布；`pg_sleep` 构造「失租但数据库活动未停」场景（容量不计入、接管者单一可发布所有权、旧副本迟到发布无效）；双 Worker 容量满时并发领取均落空、每运行至多一个有效所有权；执行期间心跳续租保持单一 generation（3s 执行 > 2s 租约）。

**实际验证：** `COMPOSE_PROJECT_NAME=zcode-glm-53-xhigh API_HOST_PORT=18001 WEB_HOST_PORT=15174 ./dev test` 全绿——api-test 191 通过（Worker 接缝 13 个用例连续 3 轮稳定）、web-test 33 通过、e2e 4 通过；`datasets/sales-analytics-v1` 运行 `python3 validate.py` 通过；`git diff --check` 干净。

**Review：** `/code-review` 双轴复核：Standards 无硬违规，修复了接管分支与 `_finish_attempt` 的重复 SQL、发布栅栏片段去重；Spec 无功能缺陷，按发现补齐心跳间隔续租的执行期证据与「每运行至多一个可发布所有者」的显式断言。`cancelling` 失联收敛确认属 06 范围。

**保留风险：** 尝试上限与自动尝试策略在 05（当前接管无次数上限，依赖 05 交付）；顺手修复了与本票无关的 web 既有偶发（`stops polling when the workbench unmounts` 在负载下把卸载前的合法首次轮询计为失败，改为卸载前清空 mock、只断言卸载后行为）。

**提交：** 见本分支后续 commit（feat(异步查询): 交付租约心跳接管与全局容量；test(工作台): 修复卸载轮询断言的负载偶发）。
