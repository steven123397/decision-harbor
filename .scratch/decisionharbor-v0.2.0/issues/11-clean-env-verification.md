# 11 — 干净环境交付验证与回归收口

**What to build:** 在全新环境中从零交付完整产品：迁移、seed（重复执行为无操作、冲突 fail-closed）、Web/API/Worker 就绪、并行 Compose 项目隔离、运行中重启恢复全部成立；保留基线回归全景通过；统一测试入口失败返回非零退出码；同步时代遗留配置、环境示例与运行文档收口到三进程拓扑。

**Blocked by:** 10 — 工作台异步体验完整化（传递覆盖 01–09）

**Status:** resolved

- [x] 全新环境从零启动：bootstrap 完成角色/数据库/授权与迁移；seed 重复执行为无操作，版本、摘要或行数冲突时按 ADR-0008 以 `seed_conflict` 失败并完整回滚；Web、API、Worker 就绪探测全部通过
- [x] `/ready` 以独立最小权限身份在 1 秒截止时间内检查两个数据库、迁移和 seed 标记，不就绪返回 503 `service_not_ready`；`/health` 只表达进程存活
- [x] 运行中重启恢复：Worker 失联后由租约接管或收敛，系统达到一致状态，没有永久卡住的运行
- [x] 保留基线回归全景通过：SQL AST 默认拒绝、允许对象范围、系统对象与绕过探针、analytics 真实只读权限、固定数据口径、超时与行数限制
- [x] 错误脱敏：所有已知失败映射为稳定错误码与摘要，未知异常收敛为 `internal_error`；外部响应与默认日志不泄漏数据库原始消息、堆栈、DSN、凭据或原始 SQL
- [x] 并行 Compose 项目隔离保持；`./dev test` 任一环节失败返回非零退出码
- [x] 同步时代遗留（配置键、文档、注释）清理完毕；环境示例与运行文档反映 API/Worker/Web 三进程拓扑

## Resolution

**结果：** 收口三处运行期缺口并完成全部交付验证；无新增迁移。

- **就绪截止时间统一**：`readiness.py` 新增共享 `DeadlineBoundedCheck`（1 秒截止、超时/异常一律未就绪、探测不叠加并发），API 与 Worker 的 `/ready` 都经它判定——此前 Worker 的 `/ready` 直接同步执行探测、无整体截止时间。`/health` 仍只表达进程存活。单元测试以挂起探测证明两进程的 `/ready` 均在截止时间内返回 503 `service_not_ready`。
- **错误脱敏收口**：
  - `api.py` 增加 catch-all 中间件：未被业务映射覆盖的未知异常收敛为 500 `internal_error` envelope，日志只记录方法与路径（无堆栈）；以损坏快照负载触发并断言原始解析错误不进入响应。
  - `worker.py` 维护路径日志（领取循环、续租、取消维护、清理、数据库取消请求）由 `exc_info=True` 改为 `_warn_maintenance`（事件 + 异常类型名），默认日志不再出现堆栈、数据库原始消息或 DSN；测试以不可达数据库驱动真实维护失败并断言。
  - `config.py` 新增 `load_or_exit`：API 与 Worker 的非法/缺失配置统一以稳定消息退出码 2 失败（此前 API 配置错误直接抛 traceback；Worker 已有行为收敛到共享实现）；uvicorn factory 下 `SystemExit(2)` 的干净退出已实测。
  - `bootstrap.py` 的 `SeedConflict`（ADR-0008 已知失败）以 `bootstrap failed: seed_conflict...` + 退出码 1 干净失败，不再打印堆栈；单元测试固化。
- **三进程拓扑收口**：`.env.example` 补齐缺失的 `WORKER_CLEANUP_INTERVAL_MS`；README「本地运行」补三进程（Web/API/Worker）+ init 一次性引导与 seed 语义说明。全仓扫描（源码、测试、迁移、compose、文档）确认无同步时代遗留配置键、注释或文档——剩余「同步」字样均为 spec 的合法表述（提交路径同步策略判定）或 ADR/archive 历史语境。

**实际验证：**

- **干净环境从零**：两个全新 Compose 项目（独立卷与端口）上 `./dev test` 全绿——api-test 277 通过（271 基线 + 6 新增）、web-test 52 通过、e2e 10 通过，退出码 0；`--wait` 门控 Web/API/Worker 全部就绪探测。
- **seed 幂等与冲突（实测）**：已初始化卷上重跑 `init` → `bootstrap complete: dataset unchanged`（退出码 0）；篡改 `maintenance.dataset_seeds` 摘要后重跑 → 稳定消息 + 退出码 1、无堆栈、行数不变（完整回滚、不覆盖）；篡改期间 API `/ready` 返回 503 `service_not_ready` 而 `/health` 保持 200（就绪判定真实依赖 seed 标记），恢复标记后 `/ready` 回到 200。`test_seed.py` 自动化覆盖同口径。
- **运行中重启恢复（实测）**：提交 4 表笛卡尔积长查询进入 running 后 `docker compose kill worker`，重启后新进程（不同 worker_id）在租约过期后以 generation 2 接管并收敛为稳定 `failed(query_timeout)`；宕机期间入队的另一运行重启后正常执行 `succeeded`；全库无 60 秒以上未终态运行。集成层 `test_recovery_when_worker_dies_at_three_points` 覆盖三个失联时点。
- **并行隔离（实测）**：两个项目（closure 18010/15180 与 parallel 18020/15185）同时就绪（双方 `/ready`、Web 均 200），在 parallel 提交的运行不出现在 closure 的历史（limit=100 检索确认），卷与容器各自独立。
- **非零退出码（实测）**：向 `tests/conftest.py` 注入故意失败后 `./dev test` 以退出码 4 中止（后续环节未执行）；移除后最终全量运行退出码 0。
- **TDD 纪律**：6 个新增/改写测试对改动前实现全部失败（stash 源码验证 red），实现后全绿。
- `datasets/sales-analytics-v1` 运行 `python3 validate.py` 通过；`git diff --check` 干净；验证项目、卷与镜像已全部清理。

**Review：** `/code-review` 双轴复核。Standards 无硬违规；按判断项修复三处——API/Worker 重复的配置失败处理抽为 `config.load_or_exit`、测试常量更名 `UNREACHABLE_DATABASE_URL`、就绪截止参数统一命名 `readiness_deadline_seconds`。Spec 轴四点解释性确认（见保留风险）；`WORKER_CLEANUP_INTERVAL_MS` 入 `.env.example` 与 API 配置干净退出判为脱敏/拓扑收口的合理延伸。修复后单元 183 通过、最终全量在第三个全新项目复跑全绿（277/52/10，退出码 0）。

**保留风险：**

- `/ready` 的 platform 侧探测复用 `platform_app`（API 本就持有的最小 platform 身份，探测只读 `alembic_version`）；spec 第 56 行「独立的最小就绪探测凭据」按单数理解为 analytics 侧专属角色（`analytics_readiness` 已隔离）。如需 platform 专属只读角色需新增迁移，超出本票范围。
- `DeadlineBoundedCheck` 在一次探测超时后的排空期内，后续 `/ready` 立即返回未就绪（保守、fail-closed 方向）；底层探测自带 1s connect/1s statement 超时，会自然收敛。为既有 API 语义，本票共享给 Worker 而未改变。
- 一次性 `init` 容器的未知基础设施故障（如连不上数据库）仍打印 traceback：属运维诊断信息，不含凭据/DSN/用户 SQL；已知失败（`seed_conflict`、配置错误）已全部干净收敛。
- 长时运行的幂等键空间无保留期清理（07/08 已记录的既有风险，与审计事实同寿命）。

**提交：** 见本分支 commit（feat(异步查询): 交付干净环境验证与回归收口）。
