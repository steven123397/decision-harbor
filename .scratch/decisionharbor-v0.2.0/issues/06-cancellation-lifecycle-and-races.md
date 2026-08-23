# 06 — 取消请求链路与发布竞态

**What to build:** 用户可以取消排队或运行中的查询：`queued` 直接取消且不会被 Worker 领取；`running` 先持久化取消意图进入 `cancelling`，再 best effort 请求数据库取消。重复取消幂等，不制造新状态或错误。取消与终态发布以条件更新竞争：取消意图先记录则取消获胜、之后返回的结果被丢弃；成功终态与结果已原子提交则迟到取消只返回既有事实。`cancelling` 不会无限停留：所有者失联时由接管/清理循环在租约过期后的一个轮询周期内收敛。依据 CONTEXT.md「取消请求」术语与 spec 取消小节。

**Blocked by:** 04 — 租约、心跳、接管与全局容量

**Status:** resolved

- [x] `POST /api/v1/query-runs/{id}/cancel` 按合同返回：`queued` 直接取消并返回 200；`running`/`cancelling` 返回 202；已 `cancelled`/`succeeded` 返回 200 与既有终态；`rejected`/`failed` 返回 409 `query_run_not_cancellable`
- [x] `queued` 运行取消后进入 `cancelled` 且不能被 Worker 领取
- [x] `running` 运行取消先持久化取消意图并进入 `cancelling`，再 best effort 请求数据库取消；数据库取消失败不撤销取消意图，之后返回的结果仍被丢弃
- [x] 取消意图持久化后不得领取新执行尝试；当前所有者在数据库工作结束或取消请求返回后收敛为 `cancelled`
- [x] 所有者失联时，接管/清理循环在租约过期后的一个轮询周期内把 `cancelling` 收敛为 `cancelled`
- [x] 重复取消幂等，不制造新状态或错误
- [x] 两种竞态顺序均有证据：取消意图先于终态发布记录则取消获胜并丢弃结果；成功终态与结果已原子提交则迟到取消返回既有成功事实
- [x] 取消后的运行不再出现新执行尝试（自动尝试不绕过取消）
- [x] 取消事实（时间、状态迁移、关联执行尝试）进入审计事实

## Resolution

**结果：** 交付取消请求全链路：API 合同、条件更新竞态、Worker best effort 数据库取消与双路径收敛。

- **API/意图持久化**：`QueryRunRepository.cancel` 以有限次「重读 + 条件转移」与领取/终态发布竞争（行锁仲裁，状态只前进）：`queued→cancelled` 直接终态化（200），`running→cancelling` 先持久化意图（202），`cancelling` 重复取消幂等命中（202），`cancelled`/`succeeded` 返回既有终态（200），`rejected`/`failed`/`received` 返回 409 `query_run_not_cancellable`，未知运行 404。`received` 为提交事务内的瞬时状态（合同未枚举），按不可取消处理以避免与提交侧条件转移互踩；service/api 增量映射，新错误码 `query_run_not_cancellable` 进入 HTTP 状态表。
- **数据库 best effort 取消**：遵守模块边界（API 只持 platform 凭据），由 Worker 心跳维护路径 `request_pending_cancellations` 对本人持有的 `cancelling` 运行发起——执行器注册执行中后端 pid（按 run_id 键），`cancel_active` 经独立 NullPool 连接发送 `pg_cancel_backend`（同角色可取消；read-only 事务不阻止）；单次失败仅告警不撤销意图、不中断维护。取消的查询以 57014 中止并沿既有 `query_timeout` 映射返回，随后被取消栅栏挡下。
- **收敛双路径**：当前所有者在 `process()` 末尾 `_converge_cancelled`（所有权栅栏：本人当前尝试且租约未过期）；失联运行由 `converge_expired_cancellations` 收敛（租约已过期的 `cancelling`，幂等条件更新），在 `run_once` 每轮询周期与心跳线程各驱动一次——空闲副本按轮询周期收敛，单副本忙于长执行时按心跳周期兜底。`cancelling` 不进入领取候选（04 已排除），自动尝试释放与终态发布均被 `status='running'` 栅栏挡下，取消不可绕过。
- **审计事实**：取消时间（`finished_at`）、状态迁移（`cancelled` 终态，满足既有 CHECK：无错误码、无结果计数）、时长（`duration_ms`）与关联执行尝试（`claimed_at`/`finished_at`/`worker_id`）落在 `query_runs` + `execution_attempts`，GET 运行事实可读；无独立迁移日志表（CONTEXT.md 审计事实定义不要求）。
- 迁移 `platform_0006`：`query_runs_cancelling_idx`（`WHERE status='cancelling'` 部分索引）服务收敛扫描；readiness platform 头推进到 `platform_0006`。

**测试证据**（`tests/integration/test_cancellation.py`，真实 PostgreSQL/HTTP，7 用例）：全状态 HTTP 合同（含 404/幂等/审计事实可读）；queued 取消不可领取且零执行尝试；running 取消后查询自然结束→结果丢弃→所有者收敛；发布前记录取消→迟到成功/失败发布均无效→收敛（竞态顺序一）；succeeded 后迟到取消返回既有成功事实且快照完好（竞态顺序二）；pg_stat_activity 观察 FETCH 真实执行后取消→20s 查询数秒内中止收敛（best effort 生效）；取消请求抛错→意图保留→自然结束结果仍丢弃；租约过期后一次 `run_once` 收敛且无新尝试；`analytics_unavailable` 自动尝试被取消栅栏挡下（更新 05 的 `test_recorded_cancellation_intent_blocks_auto_retry`：其断言从「停在 cancelling」更新为「所有者收敛为 cancelled」，该收敛本就是 06 范围）。单元测试补 API 合同映射（FakeService 五分支）与 service 取消映射。

**实际验证：** `COMPOSE_PROJECT_NAME=zcode-glm-53-xhigh API_HOST_PORT=18001 WEB_HOST_PORT=15174 ./dev test` 全绿——api-test 216 通过、web-test 33 通过、e2e 4 通过；`datasets/sales-analytics-v1` 运行 `python3 validate.py` 通过；`git diff --check` 干净；运行中 Worker 应用 0006 后健康无维护错误刷屏。（首轮全量跑 e2e 有 1 例负载偶发失败，隔离重跑与后续全量均通过，模式与 04 记录的容器 CPU 节流偶发一致。）

**Review：** `/code-review` 双轴复核：Spec 无功能缺陷（逐条验收核对通过），记录两处解释性确认——数据库取消由 Worker 心跳异步发起（凭据边界使然，符合 spec「best effort」）、`received` 按 409 处理；Standards 无硬违规，按发现修复四处：api.py 导入风格统一括号形式、两处 ServiceFailure 处理器去重为 `_failure_response`、worker 收敛 SQL 骨架去重为 `_CANCELLING_TARGET_SQL`、`cancel_key` 收紧为 `str` 并补收敛日志。修复后 api 套件复跑 216 通过。

**保留风险：** 工作台取消交互（按钮、状态展示）属 10；历史分页属 09。e2e 负载偶发为既有已知问题，非本票引入。

**提交：** 见本分支后续 commit（feat(异步查询): 交付取消请求链路与发布竞态）。
