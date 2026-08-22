# 05 — 执行尝试上限与错误分类

**What to build:** 基础设施故障最多触发 3 次自动执行尝试：只有租约丢失（含 Worker 进程失联进入的租约路径）与 `analytics_unavailable` 触发自动尝试；`query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type`、`internal_error` 直接进入 `failed`，不自动尝试。尝试耗尽后运行进入稳定的 `failed` 终态；自动尝试不修改 SQL、不绕过策略判定、不覆盖取消。查询运行与执行尝试保留稳定关联与时间事实，可重建一次查询的执行历史。

**Blocked by:** 04 — 租约、心跳、接管与全局容量

**Status:** ready-for-agent

- [ ] 领取后未产生终态即失租的运行被自动重新尝试；当前所有者遇到 `analytics_unavailable`（连接或会话中断）时自动创建新执行尝试
- [ ] `query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type`、`internal_error` 直接进入 `failed`，不自动尝试
- [ ] 每个查询运行最多 3 次执行尝试（`WORKER_MAX_EXECUTION_ATTEMPTS`）；耗尽后进入稳定 `failed` 终态，不再领取
- [ ] 自动尝试不改变 SQL、不重新触发策略判定以绕过治理、不覆盖已记录的取消意图
- [ ] 恢复测试在领取后、查询执行中、终态发布前分别终止 Worker，证明租约接管、尝试上限与旧 generation fencing
- [ ] 查询运行与执行尝试有稳定关联与时间事实，可重建一次查询的执行历史
