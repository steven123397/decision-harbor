# 05 — 接管失租运行并限制自动执行尝试

**What to build:** 让 Worker 崩溃、失联或 analytics 临时不可用后，查询运行可以由新所有者恢复，同时阻止旧执行者发布。自动恢复有明确适用范围和次数上限，不会把永久错误变成无限循环。

**Blocked by:** 03 — 协调双 Worker 的全局执行容量；04 — 原子发布精确受限的结果快照

**Status:** claimed

- [ ] 租约过期后，其他 Worker 可以为同一查询运行创建更高 generation 的执行尝试；接管期间运行保持 `running`，不回退到 `queued`。
- [ ] 旧 generation 对状态、错误或结果的任何迟到发布均不产生效果；系统只保留一个当前可发布所有者。
- [ ] 租约丢失和当前所有者遇到 `analytics_unavailable` 时可以自动尝试；Worker 进程失联通过租约丢失进入同一路径。
- [ ] `query_timeout`、`query_semantic_error`、`result_too_large`、`unsupported_result_type` 和 `internal_error` 直接形成稳定 `failed` 终态，不自动尝试。
- [ ] 每个查询运行最多产生 `WORKER_MAX_EXECUTION_ATTEMPTS` 次执行尝试，默认值为 3；非法上限配置使 Worker 非零退出，尝试耗尽后运行稳定失败。
- [ ] 自动尝试复用原 SQL 和治理事实，不改变输入、不绕过策略，也不创建新的查询运行；每次尝试和最终结论保留可重建生命周期的审计事实。
- [ ] Worker 对失租数据库活动尽最大努力请求取消，但恢复不依赖物理停止确认；测试分别观测有效所有权和发布栅栏，不承诺物理 exactly-once。
- [ ] 故障注入在领取后、analytics 执行中和终态发布前终止 Worker，证明接管、尝试上限、错误分类和旧 generation fencing。
