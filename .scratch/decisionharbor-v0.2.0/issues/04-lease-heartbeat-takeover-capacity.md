# 04 — 租约、心跳、接管与全局容量

**What to build:** 平台维护者可以运行 2 个 Worker 副本共享数据库协调的全局并发上限（默认 4）：Worker 按心跳间隔续租；进程失联后租约过期，其他副本接管运行并创建 generation+1 的新执行尝试，运行保持 `running` 不回退；旧 generation 永远不能发布状态或结果。依据 ADR-0005（有效执行所有权）。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** ready-for-agent

- [ ] Worker 持有期间按 `WORKER_HEARTBEAT_MS` 间隔续租；所有权按 `WORKER_LEASE_MS` 过期
- [ ] 租约过期后其他 Worker 可接管并创建新执行尝试（generation 递增）；接管期间查询运行保持 `running`，不回退 `queued`
- [ ] 旧 generation 的任何状态或结果发布不产生效果；同一运行同一时刻最多一个可发布所有者（当前 generation 且租约未过期）
- [ ] 两个 Worker 副本同时运行时，数据库中未过期的有效执行所有权从不超过 4
- [ ] 容量判定不把已失租但尚未物理停止的旧数据库活动计为有效所有权
- [ ] 双 Worker、租约过期、接管、旧 generation 场景用真实 PostgreSQL 事务与约束构造（基于数据库的 Worker 集成接缝），不以 Mock 代替
