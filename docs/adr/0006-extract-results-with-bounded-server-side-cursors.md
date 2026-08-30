---
status: implemented
date: 2026-07-20
---

# 使用服务端游标有界提取查询结果

## Context

SQL 策略只能限制查询形态，不能预知结果基数。若执行器先把完整结果拉入 API 或 Worker 内存，再在响应层截断，行数上限无法约束数据库传输、连接占用和进程内存。

## Decision

analytics 执行器使用服务端游标按有限批次提取，并在已能判定稳定结果前缀和 `truncated` 后停止读取。连接池与执行容量保持有界；事务退出时必须提交或回滚，不把未完成游标和会话状态泄漏给后续运行。

## Considered options

- 使用 `fetchall` 后截断：拒绝。外部响应虽然有限，执行进程的内存与传输仍可能无界。
- 只依赖 SQL 自动改写添加 `LIMIT`：拒绝。它会改变部分查询语义，也不能独立证明连接池和事务收敛。

## Consequences

结果行数和内存成本可由执行边界约束，但 Worker 必须正确关闭游标、事务与连接。v0.2.0 增加字节预算后仍应增量计算前缀，不能退回完整物化。

## Implementation and evidence

提交 `6e3964f` 的 `PostgresQueryExecutor` 使用命名服务端游标、`fetchmany(max_rows + 1)`、有界连接池和事务上下文；集成测试覆盖真实 PostgreSQL 上的行数截断、只读权限和语句超时。v0.2.0 的 ticket 03 起它只由 `decisionharbor.worker` 调用，执行不再占用 HTTP 请求生命周期；`tests/worker/test_query_execution.py` 证明成功、语义错误、未知类型与超时路径之后池化连接仍可复用。ticket 04 把单次 `fetchmany(max_rows + 1)` 改为以 `FETCH_BATCH_SIZE` 为上限的循环：每批只取“还能进入快照的行数 + 1”，一旦某行被 `ResultSnapshotBuilder` 拒绝就停止读取。字节预算因此没有把执行路径带回完整物化，超大结果也只累积到预算为止。取消与失租路径的游标收尾由 ticket 09 与 07 补齐。

## Revisit when

结果改为数据库原生导出或流入外部对象存储，且新链路能提供等价的内存、连接和事务边界时，重新评估服务端游标。
