# 03 — 结果保留期、过期语义与幂等清理

**What to build:** 成功结果快照在 `finished_at + 24h` 后明确过期：用户读取过期结果收到 HTTP 410 `result_expired`，而不是误以为查询失败或不存在；运行事实与审计事实在过期后仍完整可读。平台侧清理例程可以重复执行：只删除结果内容，保留长期审计事实，调度重叠或进程重启不会破坏状态。

**Blocked by:** 02 — 结果快照大小预算与截断矩阵

**Status:** resolved

- [x] `finished_at + 24h` 内 `GET /api/v1/query-runs/{id}/result` 正常返回快照；过期后返回 410 `result_expired`
- [x] 过期后 `GET /api/v1/query-runs/{id}` 仍返回完整运行事实与审计事实
- [x] 清理只删除结果内容，不删除查询运行、执行尝试或审计事实
- [x] 清理操作幂等：重复执行、调度重叠或进程重启后再次执行均为无操作，不产生错误或不一致状态
- [x] 集成测试用真实数据库构造跨保留期边界的记录，并证明清理可重复执行

## Resolution

**结果：** 交付结果保留期、过期语义与幂等清理。

- `cleanup.py`：共享过期口径 `is_expired(finished_at)`（严格越过 `finished_at + 24h` 才过期，读取与清理两侧同一边界），`ResultRetentionCleaner.cleanup_once` 以单条 `DELETE ... WHERE run_id IN (SELECT id FROM query_runs WHERE finished_at IS NOT NULL AND finished_at < :cutoff)` 幂等删除过期快照；时钟注入支持测试且默认 UTC aware。
- `api.py`：结果端点在 `succeeded` 且越过保留期时返回 410 `result_expired`（快照是否已被清理删除不影响判定——succeeded 运行事实证明结果曾成功发布）；保留期内快照缺失仍为 409 `result_unavailable`；运行事实端点不受过期影响。
- `worker.py`：`run_forever` 增加 `worker-cleanup` 守护线程按 `WORKER_CLEANUP_INTERVAL_MS`（默认 300,000 ms，正整数且 ≤ 86,400,000，非法配置非零退出）周期执行清理；失败仅告警不阻塞领取与执行。
- 迁移 `platform_0003`：`platform_worker` 获得 `result_snapshots` DELETE 权限；`query_runs_finished_at_idx` 部分索引谓词与清理查询一致（`WHERE finished_at IS NOT NULL`）。就绪探测版本推进到 `platform_0003`；compose 透传新配置键。
- 测试：单元层覆盖过期口径边界一致性（恰好 24h 不过期）、保留期内可读、过期 410、清理后无快照仍 410 而非 409、清理幂等与不触碰未完成运行、配置校验；真实数据库集成层（`test_result_retention.py`，8 个用例）覆盖保留期内可读、过期 410 且审计事实完整（含执行尝试）、清理只删结果内容、重复执行/进程重启/并发重叠清理均为无操作或一致状态、`platform_worker` 真实授权下删除。

**实际验证：** 销毁卷重建干净环境后 `API_HOST_PORT=18000 WEB_HOST_PORT=15173 COMPOSE_PROJECT_NAME=decisionharbor-zcode-glm53-xhigh ./dev test` 全绿——api-test 164 通过、web-test 33 通过、e2e 4 通过，退出码 0；`validate.py` 通过；`git diff --check` 干净。

**Review：** `/code-review` 双轴复核。Standards：无硬性违规；修复三处判断项——清理时钟默认值由 naive 本地时间改为 UTC aware（非 UTC 宿主上会漂移的真实缺陷）、过期谓词抽取为读取/清理共享的 `is_expired`（消除双份口径）、迁移索引谓词与清理查询对齐使部分索引可被使用；移除配置字段死默认值。Spec：两轴均无缺失项；按评审补充调度重叠的并发清理测试与 `is_expired` 恰好 24h 边界测试。有意保留：`succeeded` 且 `finished_at IS NULL` 的防御分支归入 410（数据库 CHECK 约束使该形态不可达，防御深度保留）。

**保留风险：** Web 工作台的过期/不可用反馈文案属 10 号票；清理目前由 Worker 进程承载，独立清理进程或管理入口不在本版本范围。
