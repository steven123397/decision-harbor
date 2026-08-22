# 异步查询运行生命周期设计

## 1. 目标与边界

本设计定义 v0.2.0 在首轮同步 SQL 查询链路之上的异步执行能力：提交后立即返回查询运行标识，由持久化队列和独立 Worker 执行，客户端通过轮询读取状态和结果。

本版本仍只处理显式、受 SQL AST 治理的查询，不引入 LLM、自然语言转 SQL、RAG、MCP、A2A、外部消息队列、SSE/WebSocket、批处理、优先级、定时任务、图表、保存查询、租户或 RBAC。前端只补充状态、结果和错误的清晰反馈，不设独立视觉评分目标。

## 2. 规范语言与状态

一次用户提交形成一个 `Query Run`。状态集合为：

`received → rejected | queued → running → succeeded | failed`

运行中的取消使用 `cancelling → cancelled`；排队中的取消可直接进入 `cancelled`。`rejected` 表示同步策略校验未通过，不进入队列；`failed` 表示已执行但未成功；`cancelled` 表示取消请求获胜并且结果不得发布。

状态只允许通过带当前状态、lease generation 和版本条件的原子更新推进。终态不可逆。取消若在终态发布 CAS 之前被记录，则取消获胜，即使底层数据库查询已经返回；若 `succeeded` 与结果已原子持久化，之后不可改为 `cancelled`。

每次自动尝试都有 `attempt`、`worker_id`、`lease_generation`、租约过期时间和心跳时间。租约接管不承诺物理 exactly-once；旧 generation 永远不能发布状态或结果。

## 3. 提交与策略边界

API 在事务内完成请求幂等检查、SQL 大小和 AST/对象策略校验，并写入 `received` 或 `rejected`。校验通过后写入 `queued`，提交接口返回 HTTP 202。相同幂等键重复提交相同 SQL 返回原运行；同键不同 SQL 返回稳定冲突错误。策略拒绝返回 HTTP 422 与 `rejected` 运行记录，用户修改 SQL 后重新提交。

平台数据库保存查询运行、审计、幂等键、租约和有限结果快照；用户 SQL 只能使用独立的 analytics 只读身份。Worker 同时持有平台任务凭据和 analytics 查询凭据，API 不执行用户查询。

## 4. 队列、Worker 与恢复

- 队列是平台 PostgreSQL 中的持久记录；默认一个 Worker，必须支持两个 Worker 副本。
- 所有 Worker 共享全局并发上限 4。领取任务使用行锁/条件更新，不能依赖进程内计数。
- 默认配置：`QUERY_MAX_CONCURRENCY=4`、`WORKER_LEASE_MS=15000`、`WORKER_HEARTBEAT_MS=3000`、`WORKER_POLL_MS=250`、`WORKER_MAX_EXECUTION_ATTEMPTS=3`；启动时拒绝非法配置。
- Worker 领取任务生成新的 lease generation，周期性心跳。租约过期后其他 Worker 可接管，最多自动尝试 3 次；自动重试不改变 SQL 语义、不绕过取消，也不复制终态结果。
- 停止、超时或数据库异常必须产生可读的 `failed` 错误码。取消是幂等的：排队任务不执行，运行任务向执行器发出 best-effort 取消请求；取消请求成功记录后，执行结果必须被发布栅栏丢弃。

## 5. 结果快照与保留

成功状态与结果快照在同一事务中原子发布。快照最多 500 行、1 MiB；按稳定顺序截断并记录 `truncated`。单行超过字节上限时运行进入 `failed/result_too_large`。结果保留 24 小时，以数据库 `finished_at + 24h` 判断过期；审计记录长期保留。清理任务可重复执行。

结果 API 返回：未完成为 HTTP 409，已过期为 HTTP 410，状态存在但结果不可用为 HTTP 409。结果查询不重新执行 SQL。

## 6. 外部接口

- `POST /api/v1/query-runs`：提交 `{sql, idempotency_key?}`，成功排队返回 202；策略拒绝返回 422。
- `GET /api/v1/query-runs/{id}`：返回状态、审计事实、错误摘要、attempt 和时间字段。
- `GET /api/v1/query-runs/{id}/result`：返回有限快照或 409/410。
- `POST /api/v1/query-runs/{id}/cancel`：取消请求，重复调用返回同一最终事实。
- `POST /api/v1/query-runs/{id}/retry`：仅 `failed`/`cancelled` 可重试，形成新的运行并以 `retry_of` 关联；`rejected` 必须编辑后重新提交，`succeeded` 不可重试。
- `GET /api/v1/query-runs`：按创建时间分页读取历史运行，不能包含其他用户或租户概念。

所有响应沿用统一 `{data, error}` 结构，错误码稳定且不泄漏凭据或内部 SQL。客户端只轮询 GET 接口，不依赖实时推送。

## 7. 验证接缝

测试必须覆盖状态转换和非法转换、同键幂等与冲突、取消竞态、终态发布 fencing、双 Worker 并发上限、租约过期接管、自动尝试上限、超时/重启恢复、快照大小/过期/清理、retry 关系及 v0.1 SQL 策略回归。浏览器主链至少覆盖排队、运行、成功、失败、取消和过期/不可用反馈。
