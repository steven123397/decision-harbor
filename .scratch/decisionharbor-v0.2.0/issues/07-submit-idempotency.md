# 07 — 提交幂等键

**What to build:** 分析用户重复发送同一幂等请求时得到同一查询运行：提交接口支持 `Idempotency-Key` 请求头，键为 1 到 128 个可见 ASCII 字符。相同键与完全相同输入返回原运行（允许查询的重放返回 202，策略拒绝的重放返回 422 与原 `rejected` 运行）；相同键与不同 SQL 返回 409 `idempotency_conflict`；未提供键时每次请求创建新运行。当前版本没有用户或租户，键作用域为整个产品实例。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** resolved

- [x] 未提供键时每次请求创建新查询运行
- [x] 相同键与完全相同输入的重放返回原运行：允许查询返回 202，策略拒绝返回 422 与原 `rejected` 运行，均不创建新工作
- [x] 相同键与不同输入返回 409 `idempotency_conflict`
- [x] 键非法（空、超过 128 字符、含不可见 ASCII 或非 ASCII 字符）返回 422 `invalid_idempotency_key`
- [x] 键作用域为整个产品实例；重放不产生重复的查询运行或重复执行
- [x] 错误响应沿用统一 `{data, error}` envelope 与稳定错误码

## Resolution

**结果：** 交付提交幂等键：`Idempotency-Key` 请求头（1 到 128 个可见 ASCII 字符）下，相同键与完全相同输入重放原运行，不同输入稳定冲突，无键每次新建。

- 迁移 `platform_0004`：`idempotency_keys` 表，主键 `(scope, key)`，`run_id` 外键到 `query_runs`，仅授予 `platform_app` SELECT/INSERT；就绪探测推进到 `platform_0004`。
- `repository.py`：`request_fingerprint`（SQL 的 SHA-256）作为"完全相同输入"的稳定判定；`create` 在同一事务内插入运行与键占用；键插入的唯一冲突（SQLSTATE 23505）映射为 `IdempotencyKeyTaken`，其余完整性失败按原路径收敛为 `audit_unavailable`。`find_idempotency(scope, key)` 读取键占用。
- `service.py`：`submit(raw_sql, idempotency_key)` 先查键占用——不同输入 409 `idempotency_conflict`；相同输入按原运行状态重放（在途与成功/失败/取消终态返回原运行 202，`rejected` 以原拒绝语义 422 重放，网络重试落在执行中同样返回原运行）。未命中则创建，并发同键由唯一约束裁决，竞态败者重读后按重放返回先到的运行。
- `api.py`：头校验（1–128 字符且仅可见 ASCII 33–126），非法返回 422 `invalid_idempotency_key` 且不创建任何运行；`idempotency_conflict` 映射 409；沿用统一 envelope。
- 测试：单元层覆盖重放/冲突/竞态/执行中重放/终态重放/指纹稳定性/非法键（含以原始字节发出的非 ASCII 键）；真实数据库集成层（`test_submit_idempotency.py`，9 个用例）覆盖 202/422 重放、同键不同输入 409、无键新建、跨请求实例级作用域、非法键不建运行、重放只被 Worker 执行一次、并发同键同输入恰好一个运行、并发同键不同输入恰好一个赢家。键与 SQL 加一次性标记避免历史遗留数据影响断言。

**实际验证：** `API_HOST_PORT=18000 WEB_HOST_PORT=15173 COMPOSE_PROJECT_NAME=decisionharbor-zcode-glm53-xhigh ./dev test` 全绿——api-test 185 通过、web-test 33 通过、e2e 4 通过，退出码 0；`validate.py` 通过；`git diff --check` 干净。

**Review：** `/code-review` 双轴复核。Standards：无硬性违规；修复三处判断项——重复的 `ServiceFailure` 构造抽为 `_conflict()`/`_audit_unavailable()`、`IntegrityError` 映射收窄到键唯一冲突（SQLSTATE 23505，避免误把审计存储故障当成客户端冲突）、`_idempotency_key_error` 更名。Spec：两处实质缺陷已修——running/cancelling 中的重放返回原运行而非 503（网络重试可在执行期到达）；空键在传输层等同缺失由无键路径覆盖、直达人层仍拒绝。按评审补充执行中重放的单元测试。

**保留风险：** 重试键按来源运行 ID 隔离在 08（本票只交付 `scope='submit'` 的提交键）；幂等记录没有保留期清理（键空间随时间增长，与审计事实同寿命，超出本票范围）。
