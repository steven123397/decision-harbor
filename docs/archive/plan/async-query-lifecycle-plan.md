# v0.2.0 异步查询执行计划

## 目标

在不破坏首轮同步 SQL 治理、固定数据和双数据库隔离的前提下，交付可恢复的 PostgreSQL 持久队列、独立 Worker、查询运行 API、有限结果快照、取消/重试/历史和最小工作台反馈。

## 顺序与完成条件

1. **领域模型与迁移**：增加查询运行状态、幂等键、attempt、lease generation、取消请求、retry 关联、结果快照和保留时间字段；迁移可重复执行，状态约束和索引覆盖领取、历史和过期清理。
2. **同步提交与轮询 API**：策略校验仍同步完成；排队返回 202；实现状态、结果、取消、重试、历史接口及稳定错误语义；先补 API 合同测试。
3. **Worker 执行闭环**：实现 PostgreSQL 领取、全局并发限制、心跳、租约接管、generation fencing、最多三次自动尝试和优雅停止；用两个副本和故障注入测试竞态。
4. **结果与清理**：实现原子终态/快照发布、500 行/1 MiB 限制、24 小时过期、幂等清理和 `result_too_large`；覆盖真实 PostgreSQL 集成测试。
5. **Web 工作台**：将同步等待改为轮询，清晰呈现 queued/running/succeeded/failed/cancelling/cancelled、错误、取消、重试、历史和过期；保持响应式和基本可访问性。
6. **回归与冻结**：运行固定数据校验、API/Worker 集成、Web 单测、Playwright、双 Compose 副本和 v0.1 隐藏安全探针；记录完整 SHA、工作树、环境和未验证项后，创建 `baseline-v0.2.0`。

## 非目标

本计划不包含认证、租户/RBAC、Redis 或其他外部队列、SSE/WebSocket、优先级/调度/批量、自然语言查询、图表、严格物理 exactly-once、多区域 HA 或独立视觉改版。
