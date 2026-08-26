# 02 — 保证提交请求幂等

**What to build:** 让客户端可以安全重发查询提交请求。相同幂等键与完全相同的 SQL 始终指向原查询运行，而错误复用同一键提交不同 SQL 会得到明确冲突。

**Blocked by:** 01 — 打通持久异步查询主链

**Status:** resolved

- [x] `Idempotency-Key` 接受 1 到 128 个可见 ASCII 字符；非法键返回 HTTP 422 `invalid_idempotency_key`，且不创建查询运行。
- [x] 未提供幂等键时，每次提交都创建新的查询运行；提供键时，其作用域覆盖当前产品实例。
- [x] 相同键与完全相同 SQL 的重复或并发提交只创建一个查询运行和一份待执行工作；允许的查询每次返回 HTTP 202 和原运行。
- [x] 策略拒绝的幂等重放返回 HTTP 422 和原 `rejected` 查询运行，不重复产生审计事实或策略外执行。
- [x] 相同键与不同 SQL 返回 HTTP 409 `idempotency_conflict`，不静默复用原运行，也不创建第二个运行。
- [x] 公开 HTTP 集成证据覆盖无键、首次提交、顺序重放、并发重放、拒绝重放和冲突，所有错误继续使用统一且脱敏的 `{data, error}` envelope。

## Resolution

Ticket 02 已交付实例级幂等提交。`platform_0003` 为查询运行增加受格式约束的可空幂等键和部分唯一索引；API 在创建运行前拒绝非法键；仓储原子创建或读取原运行；Service 使用原始 SQL 的完全相等性判断重放或冲突，并在并发策略判定竞争后重放已持久化的 `queued` 或 `rejected` 事实。未提供键时仍创建独立运行。

### 验收证据

- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket02-final API_HOST_PORT=28083 WEB_HOST_PORT=25176 ./dev test`：在全新 Compose 项目中通过 101 个 API 测试、32 个 Web 测试和 4 个 Playwright 浏览器测试。
- `apps/api/.venv/bin/pytest apps/api/tests/unit -q`：80 个 API 单元测试通过；非法键在调用提交服务前返回统一 422 envelope。
- `apps/api/tests/integration/test_query_chain.py`：公开 HTTP 覆盖无键、顺序重放、并发重放、拒绝重放和尾随空格冲突；并发用 PostgreSQL 持久事实证明只存在一个查询运行和一份队列工作。
- 在 `datasets/sales-analytics-v1/` 中运行 `python3 validate.py`：固定数据集校验通过。
- `apps/api/.venv/bin/python -m compileall -q apps/api/src apps/api/tests` 与 `git diff --check 5528873...HEAD`：通过。

### Review

- 首轮 Standards 为 0 findings；Spec 发现 1 个 P2，要求补强一条持久队列记录和原始 SQL 完全相等的验收证据。
- 提交 `eb5d144` 增加 PostgreSQL 记录断言，并以尾随空格锁定精确 SQL 边界；第二轮 Standards 与 Spec 均为 0 findings。

### 提交

- `19397a9`：实现实例级幂等提交、并发重放与稳定错误合同。
- `eb5d144`：补强持久队列唯一性和精确 SQL 冲突证据。

### 保留风险

- 当前没有单独覆盖“同键、不同 SQL 同时到达”的并发测试；该竞态与已覆盖的同键并发重放共享 PostgreSQL 唯一索引和原运行读取路径，错误输入仍由原始 SQL 完全比较返回 409。
