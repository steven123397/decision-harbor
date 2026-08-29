# 08 — 重试运行

**What to build:** 用户可以为 `failed` 或 `cancelled` 运行创建重试运行：重试创建记录 `retry_of` 关联的新查询运行并返回 202，新运行拥有独立生命周期与审计事实，进入正常治理与执行链路且可被独立取消。重试幂等键按来源运行 ID 隔离，幂等重放返回同一新运行。`rejected` 必须修改 SQL 后重新提交，`succeeded` 不可重试，其他非允许状态均返回 409 `query_run_not_retryable`。依据 CONTEXT.md「重试运行」术语。

**Blocked by:** 06 — 取消请求链路与发布竞态；07 — 提交幂等键

**Status:** resolved

- [x] `POST /api/v1/query-runs/{id}/retry` 对 `failed`/`cancelled` 运行创建记录 `retry_of` 的新查询运行并返回 202
- [x] 新重试运行进入正常治理与执行链路（策略判定、执行、取消均适用），原始运行的审计事实保持不变
- [x] 重试幂等：同一来源运行的幂等重放返回同一新运行与 202，不重复创建
- [x] `rejected` 运行重试返回 409 `query_run_not_retryable`（策略拒绝必须修改后重新提交）
- [x] `succeeded` 运行重试返回 409 `query_run_not_retryable`
- [x] 其他非允许状态（含非终态）重试返回 409 `query_run_not_retryable`
- [x] 重试关联（来源运行、时间）进入审计事实

## Resolution

**结果：** 交付重试运行：`failed`/`cancelled` 来源创建记录 `retry_of` 的新查询运行（202），新运行重走完整治理与执行链路，重试幂等按来源运行隔离，其余状态稳定 409。

- **迁移 `platform_0007`**：`query_runs.retry_of uuid REFERENCES query_runs(id)`（来源关联为审计事实）与 `query_runs_retry_of_idx`；readiness platform 头推进到 `platform_0007`。
- **`repository.py`**：`retry_idempotency_scope(source_run_id)`（`retry:<id>`）实现"重试键按来源运行 ID 隔离"；`create` 增加 `idempotency_scope` 与 `retry_of`，重试键与提交键共用 `idempotency_keys` 表（主键 `(scope, key)` 天然隔离两个命名空间），同事务插入使并发同键由唯一约束裁决。
- **`service.py`**：`retry()` 先读取来源运行——缺失 404，非 `failed`/`cancelled` 返回 409 `query_run_not_retryable`（来源是终态时不可逆，无校验后竞态）；随后复用 `_create_with_idempotency`（提交与重试共用的幂等创建骨架：预检重放 → 创建 → `IdempotencyKeyTaken` 竞态败者按重放返回）与 `_submit_new`，使新运行重走完整策略判定与 `received → queued/rejected` 链路。
- **`api.py`**：`POST /api/v1/query-runs/{id}/retry`，沿用统一 envelope；`Idempotency-Key` 头校验与提交路径共用 `_header_idempotency_key`/`_invalid_key_response`（非法键 422 `invalid_idempotency_key` 且不创建运行）。错误形态按语义区分：动作被拒（`query_run_not_retryable`）只返回错误，重试 SQL 被策略拒绝时附新 `rejected` 运行事实（与提交路径一致）。
- **测试**：单元层新增 18 个用例（service：允许/全部禁止状态/404/存储失败/幂等重放（含 rejected 重放与终态重放）/键按来源隔离/无键新建/并发竞态兜底/策略拒绝新运行；api：202 与 `retry_of`/409/404/审计失败/键透传/非法键拒绝）。集成层 `tests/integration/test_retry_runs.py`（真实 PostgreSQL/HTTP，13 用例）：真实链路 failed/cancelled 来源重试、重试运行被 Worker 正常执行且 `retry_of` 保留、重试运行独立取消且来源事实不变、同键重放仅一个新运行、键按来源隔离、无键多次新建、rejected/succeeded/queued/running/cancelling 全部 409、未知运行 404、审计事实落库、并发同键恰好一个新运行、取消后显式重试恢复、重试 SQL 仍过策略门（来源 SQL 被改写为禁止语句后重试形成新的 rejected 运行）。

**实际验证：** `COMPOSE_PROJECT_NAME=zcode-glm-53-xhigh API_HOST_PORT=18002 WEB_HOST_PORT=15175 ./dev test` 全绿——api-test 248 通过、web-test 33 通过、e2e 4 通过，退出码 0；`datasets/sales-analytics-v1` 运行 `python3 validate.py` 通过；`git diff --check` 干净。（首轮全量 e2e 有 1 例 "marks row-limited results as truncated" 偶发失败，隔离重跑与最终全量均通过，模式与 04/06 记录的容器 CPU 节流偶发一致。）

**Review：** `/code-review` 双轴复核。Standards：无硬性违规；按判断项修复三处——submit/retry 重复的幂等创建骨架抽为 `_create_with_idempotency`、两个端点重复的非法键响应抽为 `_invalid_key_response`、`include_run` 的字符串比较改为命名的 `RETRY_ERROR_ONLY_CODES`，并内联测试里的单行包装函数。Spec：无功能缺陷，两处解释性确认——重试 SQL 被策略拒绝后的同键重放返回 422 与原新 `rejected` 运行（按 spec 提交行"策略拒绝的幂等重放均返回 422"的对称语义；补充单元测试固化）；"重试关联时间"以新运行的 `created_at` 承载（CONTEXT.md 审计事实定义不要求独立时间戳）。`retry_of_idx` 为未要求的无害补充（审计查询支撑）。修复后 api 套件复跑 248 通过。

**保留风险：** 工作台重试交互（按钮、状态展示）属 10；历史分页属 09。幂等记录无保留期清理（与 07 记录的既有风险一致，键空间随审计事实同寿命）。
