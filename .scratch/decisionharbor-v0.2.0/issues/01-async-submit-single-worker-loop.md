# 01 — 异步提交与单 Worker 执行的最小闭环

**What to build:** 分析用户提交合法 SQL 后立即得到 HTTP 202 与查询运行事实；策略拒绝在提交阶段立即返回 HTTP 422 与 `rejected` 运行。独立 Worker 从 platform PostgreSQL 持久队列领取 `queued` 运行，用 analytics 只读身份执行，成功时在同一事务原子发布 `succeeded` 终态与结果快照，失败时以条件更新发布 `failed`。用户通过 `GET /api/v1/query-runs/{id}` 恢复状态，通过 `GET /api/v1/query-runs/{id}/result` 读取成功结果；Web 工作台提交后轮询到终态再渲染结果，刷新后仍可按运行标识恢复。本票是贯穿迁移、API、Worker、Web 与测试的 tracer bullet，并吸收同步实现的迁移 prefactor：API 不再执行用户 SQL。

依据 spec「Implementation Decisions」「External HTTP Contract」与 ADR-0003（PostgreSQL 持久队列）、ADR-0004（有限结果快照）、ADR-0006（有界游标提取）。快照在本票只需满足常规行数/字节边界；完整大小矩阵由 02 交付。

**Blocked by:** 无 — 可立即开始

**Status:** resolved

- [x] `POST /api/v1/query-runs` 同步完成请求校验与 SQL AST 策略判定：允许时完成 `received → queued` 并返回 202 与运行事实；拒绝时完成 `received → rejected` 并返回 422 与原运行
- [x] 策略治理边界与基线一致：AST 默认拒绝、对象允许范围、类型转换约束不因异步化扩大
- [x] 迁移把状态集扩展为 spec 的 8 个状态，数据库约束继续绑定状态事实与字段的关联
- [x] Worker 领取产生执行尝试记录（Worker 标识、generation、租约字段）；成功终态与结果快照在一个 platform 事务中原子保存；失败以条件更新发布并保留稳定错误码
- [x] 用户 SQL 只在 Worker 内以 analytics 只读身份执行，继续使用服务端游标有界提取，成功、失败与失租路径都关闭游标并结束事务
- [x] 全局并发上限默认 4，以数据库中有效执行所有权计数为准（本票单 Worker 场景下成立）
- [x] `GET /api/v1/query-runs/{id}` 存在返回 200，不存在返回 404 `query_run_not_found`
- [x] `GET /api/v1/query-runs/{id}/result`：快照可读返回 200；未完成返回 409 `result_not_ready`；终态无可读快照返回 409 `result_unavailable`；结果读取永远不重新执行 SQL
- [x] API 进程只持有 platform 凭据与就绪探测凭据；Worker 持有 platform 任务凭据与 analytics 查询凭据；运行环境编排相应调整
- [x] 同步时代遗留移除：API 内并发信号量、容量等待与启动批量失败逻辑删除；非法 Worker 配置（配置值非正整数，或心跳不严格小于租约）使对应进程启动非零退出
- [x] Web 提交后轮询运行状态到终态并渲染结果，终态后停止轮询；桌面浏览器端到端路径可用
- [x] `./dev test` 全绿（API 单元/集成、Web、e2e 按新合同更新）

## Resolution

**结果：** 交付贯通迁移、API、Worker、Web 与测试的异步最小闭环。

- 迁移 `platform_0002`：8 状态 CHECK 与新的 `query_runs_state_facts`、`execution_attempts`（worker/generation/租约）、`result_snapshots`、`current_attempt_id` 栅栏列、队列领取部分索引与 `platform_worker` 授权；存量 `running` 行以 `execution_interrupted` 收敛。
- 提交路径：`QueryRunService.submit` 只做请求校验与策略判定（`received → queued` / `received → rejected`）；API 剥离 executor、进程内信号量与启动批量失败逻辑，`Settings` 收敛为 platform + 就绪探测凭据。
- Worker（`decisionharbor/worker.py`）：容量感知领取（咨询锁串行化计数 + `FOR UPDATE SKIP LOCKED`）、心跳续租（只续当前有效所有权）、执行尝试记录、`succeeded`+快照同事务原子发布、条件发布 `failed`、旧 generation 发布被栅栏且被栅栏尝试立即终结（不泄漏容量）、独立 `/health` `/ready` 端口、非法配置 `SystemExit(2)`。
- 结果快照（`snapshots.py`）：紧凑 JSON 字节预算（固定键序/字段序、UTF-8 直计）+ 500 行上限的前缀截断与 `result_too_large` 判定已实现；02 补齐完整测试矩阵与集成证据。
- 凭据拆分：bootstrap 新增 `platform_worker` 角色；compose 中 api 不再持有 `ANALYTICS_DATABASE_URL`，worker 持有 platform 任务凭据与 analytics 查询凭据；`dev test` 在 api-test 期间停常驻 Worker 保证接缝测试确定性。
- Web：提交 → 轮询 → 终态渲染；卸载停止轮询；sessionStorage 记录活动运行，刷新后按运行标识恢复。

**实际验证：** `API_HOST_PORT=18000 WEB_HOST_PORT=15173 ./dev test` 全绿——api-test 120 通过（含 Worker 接缝集成：领取/顺序/原子发布/栅栏/容量/租约）、web-test 33 通过、e2e 4 通过（工作台异步全链路）。凭据边界由 `test_postgres_contract.py` 证明（platform_app 不能写 execution_attempts/result_snapshots，platform_worker 不能访问 analytics 等）。

**Review：** `/code-review` 双轴复核后修复：容量计数与心跳按 ADR-0005 限定「当前 generation 且租约未过期」的有效所有权、被栅栏尝试立即终结、轮询卸载停止、刷新恢复（sessionStorage）、`WORKER_HTTP_PORT` 纳入配置校验、duration SQL 与注解清理。有意保留：`HTTP_STATUS_BY_CODE` 中执行期错误码映射（状态码词汇表，后续票据端点复用）；`WORKER_MAX_EXECUTION_ATTEMPTS` 已校验但策略消费在 05。

**保留风险：** 失联 Worker 的租约接管、双副本容量证明与尝试策略未在本票范围（04/05 交付）；当前单 Worker 顺序执行，长查询期间容量上限只占 1 个槽位。

**提交：** 见本分支后续 commit（feat(异步查询)）。
