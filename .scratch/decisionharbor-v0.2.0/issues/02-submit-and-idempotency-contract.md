# 02 — 提交入队与幂等提交合同

**What to build:** 用户提交 SQL 时同步拿到策略判定结论，而不必等待队列：策略允许时立即获得一个持久的 `queued` 查询运行和 HTTP 202，策略拒绝时立即获得 `rejected` 运行和 HTTP 422。携带 `Idempotency-Key` 的重复请求在相同输入下复用同一查询运行，网络重试不会制造重复工作；相同键配不同 SQL 时得到明确冲突，而不是静默复用错误的运行。

**Blocked by:** 01

**Status:** resolved

- [x] 合法 SQL 的首次提交返回 HTTP 202，并把查询运行持久为 `queued`
- [x] 被策略拒绝的首次提交返回 HTTP 422，并把查询运行持久为 `rejected`，响应携带稳定错误码与 `query_run_id`
- [x] 提交响应不再包含查询结果，成功结果只通过结果快照在后续读取
- [x] 相同幂等键与完全相同的 SQL 返回原查询运行，不创建新运行
- [x] 相同幂等键但不同 SQL 返回 HTTP 409 `idempotency_conflict`
- [x] 未提供幂等键时每次提交创建新的查询运行
- [x] 幂等键为 1 到 128 个可见 ASCII 字符；空值、超长或含不可见字符返回 HTTP 422 `invalid_idempotency_key`
- [x] 提交键在整个产品实例内生效，当前版本没有用户或租户隔离
- [x] 请求体非法或超出大小限制返回统一 `{data, error}` envelope 与稳定错误码
- [x] 错误响应不泄漏数据库原始消息、堆栈、DSN、凭据或内部 SQL
- [x] 提交路径同步完成请求校验、SQL AST 策略判定和对象范围校验，入队前不执行用户 SQL

## Resolution

**结果：** 全部验收条件完成。提交合同现在同时覆盖同步策略判定与幂等重放：策略允许时 202 + `queued`，策略拒绝时 422 + `rejected`，相同键与相同 SQL 复用同一查询运行，同键不同 SQL 得到 409 `idempotency_conflict`。

**实际验证：**

- `./dev test` 退出码 0：`tests/unit` + `tests/integration` 170 passed，`tests/worker` 10 passed，Vitest 27 passed，Playwright 3 passed。
- 新增 `tests/integration/test_submit_idempotency.py`（8 例，含 2 个线程并发重放同一键只产生 1 个运行、1 条幂等记录）连续四次运行全部通过。
- 新增 `tests/unit/test_idempotency.py`（键边界与指纹稳定性）与 `tests/unit/test_service.py`、`test_api.py` 的重放、冲突、拒绝重放和非法键用例。
- `python3 validate.py` 通过；`git diff --check` 无输出。
- 幂等记录落库证据：集成测试直接读 `query_run_idempotency`，断言 `scope = 'submit'`、指纹等于 SQL 的 SHA-256、且一个键只对应一条记录。

**主要实现：**

- `domain.py`：`is_valid_idempotency_key`（1 到 128 个可见 ASCII，即 `[\x21-\x7e]`）、`submit_request_fingerprint`（SQL 的 SHA-256 十六进制）、`IdempotencyClaim`（scope、key、fingerprint）与 `SubmitReservation`（`is_replay` + `fingerprint_matched`）。
- `repository.reserve`：在一个事务内写入 `received` 运行与幂等记录；`INSERT ... ON CONFLICT DO NOTHING RETURNING` 抢键失败时回滚新建运行并回放赢家，队列不会留下未被键引用的重复运行。
- `service.submit`：先校验键再预留；重放路径直接回放审计事实，指纹不同报 `idempotency_conflict`，`rejected` 运行以原错误码与摘要重放；未记录的键继续走策略判定与 `received → queued/rejected`。
- `api.py`：`Idempotency-Key` 通过 FastAPI `Header` 解析后原样交给服务，错误码经 `HTTP_STATUS_BY_CODE` 映射（422 `invalid_idempotency_key`、409 `idempotency_conflict`），不新增硬编码状态分支。

**提交 SHA：** `eafc2d2`（分支 `v0.2.0/codebuddy-hy4-preview-high`，未推送；本行由随后的 tracker 关闭提交写入，实现提交本身不含自身 SHA）。

**Review 结论：** `/code-review` 双轴复核提出幂等键校验在 HTTP 层与服务层重复、硬编码 422、scope/key/fingerprint 三元组散落、重放分支重复等问题，已通过删除 HTTP 层重复校验、引入 `IdempotencyClaim`、合并单一重放出口修复；复核还提出重放不再重新执行策略判定、幂等记录没有保留期、`policy_internal_error` 分支与运行元字段暴露等观察，前两项属于本票据的有意范围（指纹相同即同一请求；幂等清理不在本票验收内，见下），后两项为 01 已交付的既有行为，不在本票改动范围。

**保留风险与后续票据：**

- 幂等记录没有保留期和清理路径。`Idempotency-Key` 表会持续增长，本票只交付合同行为；若需要与结果保留期一致的清理，应作为独立工作评估（05 的清理范围目前只覆盖结果内容）。
- 重放按记录的策略判定返回：指纹相同即视为同一请求，即使 `policy_version` 已变化也不会重新判定，以避免同一请求得到不同结论。
- 重放对已被 `recover_interrupted` 收敛为 `failed` 的运行仍返回 202（依据 spec「策略允许的首次请求或幂等重放返回 HTTP 202」按策略结论而非运行终态区分）；如需按终态区分状态码，应在 03 之后与 spec 一并确认。
- 工作台仍不发送 `Idempotency-Key`，前端重试保护属于 06 与 12 的范围。
- 本票关闭后进入 frontier 的票据：03（Worker 领取、执行与成功发布）与 11（运行历史分页）。
