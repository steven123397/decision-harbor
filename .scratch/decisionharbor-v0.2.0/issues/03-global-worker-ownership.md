# 03 — 协调双 Worker 的全局执行容量

**What to build:** 让多个 Worker 通过数据库中的有效执行所有权共享同一容量上限。横向扩展到两个副本时，每个查询运行只有一个当前所有者，且所有副本合计不会超过全局并发容量。本票会新增 platform migration，因此同时让 readiness 以随镜像交付的 Alembic head 作为唯一 schema 版本事实源，避免迁移与健康检查漂移。

**Blocked by:** 01 — 打通持久异步查询主链

**Status:** resolved

- [x] 每次领取创建带 Worker 标识、递增 generation、租约和心跳事实的执行尝试；未过期所有权不会被其他 Worker 窃取。
- [x] 两个 Worker 同时运行时，数据库中当前 generation 且租约未过期的有效执行所有权始终不超过 `QUERY_MAX_CONCURRENCY`，默认值为 4。
- [x] 同一查询运行在任一时刻最多只有一个可发布所有者；领取与容量判定由 platform PostgreSQL 协调，不依赖单进程计数。
- [x] 当前所有者按配置续租，正常停止时收敛执行资源；已经失租但尚未物理停止的 analytics 活动不再计入有效执行所有权。
- [x] 并发、租约、心跳和轮询配置使用规定默认值并接受正整数；心跳不小于租约或其他非法配置会使 Worker 在启动阶段非零退出。
- [x] API readiness 不再复制手写的 `platform_000N` 或 `analytics_000N` 版本常量，而是分别从随镜像交付的 Alembic `ScriptDirectory` 解析唯一 head，并与对应数据库的 `alembic_version` 精确比较；脚本存在多个 head、版本不匹配或元数据读取异常时继续 fail closed 为 HTTP 503 `service_not_ready`。
- [x] readiness 聚焦回归覆盖两个数据库位于当前唯一 head 时就绪，以及任一数据库版本落后、超前或脚本存在多个 head 时不就绪；全新 Compose 在本票迁移后通过 `/ready`，证明后续新增 migration 无需同步修改版本字符串。
- [x] 真实 PostgreSQL 双 Worker 集成证据同时观测全局所有权上限、单运行唯一当前所有者和未过期租约不可抢占，不以 Mock 或数据库会话数量代替所有权事实。

## Resolution

Ticket 03 已交付数据库协调的全局执行所有权。`platform_0004` 为查询运行增加当前 generation、Worker、心跳和租约事实，并新增执行尝试表；领取事务使用 PostgreSQL advisory lock 串行化全局容量判定。Worker 按配置续租，所有终态发布都核对 ownership token，正常停止会释放当前 ownership。失租后接管与自动尝试上限保留给 Ticket 05。

readiness 现在从随镜像交付的两套 Alembic `ScriptDirectory` 解析唯一 head，并与数据库版本精确比较。全量浏览器回归曾暴露并发健康检查与浏览器探测互相误判 503 的旧竞态；并发请求现共享同一个在途探测，同时继续受 1 秒截止约束。

### 验收证据

- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket03-codex API_HOST_PORT=18103 WEB_HOST_PORT=15173 ./dev test`：最终通过 124 个 API 测试、32 个 Web 测试和 4 个 Playwright 浏览器测试。
- `apps/api/tests/integration/test_worker_repository.py`：每条测试创建一次性 platform PostgreSQL 数据库并迁移到 Alembic head；两个独立 Repository 证明默认全局容量 4 和旧 generation 发布无效，两个真实 `QueryWorker` 配合真实 analytics 执行器证明容量 1 时只有一个未过期当前所有者。
- `apps/api/tests/unit/test_config.py`、`test_worker.py` 和 `test_readiness.py`：覆盖默认配置、非法启动配置、周期续租、正常停止释放、两个数据库版本落后或超前及多 head fail-closed。
- 两个 Compose Worker 副本同时启动并保持健康；`WORKER_HEARTBEAT_MS=15000` 与 `WORKER_LEASE_MS=15000` 的非法组合在启动阶段以状态 1 退出。
- 在 `datasets/sales-analytics-v1/` 中运行 `python3 validate.py`：固定数据集校验通过；`git diff --check` 与 Python `compileall` 通过。

### Review

- 首轮 Standards 发现票 05 的过期接管范围越界，并提示成功/失败发布 SQL 存在判断性的重复；Spec 发现正常停机未立即释放 ownership、双 Worker 联合证据不足，以及并发 readiness 语义扩张。
- 修复后移除过期接管，提升停止异常保护范围，增加真实双 `QueryWorker` 联合证据，并以全量 E2E 失败证明并发 readiness 修复属于本票回归所需。最终 Standards 无硬违规，Spec 无剩余发现；发布 SQL 的小范围重复保留为不阻塞交付的判断性问题。

### 提交

- `bb9853f`：实现全局执行所有权、配置与 readiness 动态 head，并补齐真实 PostgreSQL、Worker 和浏览器回归证据。

### 保留风险

- 过期运行接管、自动尝试上限、错误分类和失租 analytics 活动取消由 Ticket 05 交付；本票只让过期 ownership 不再占用全局容量，不会提前重新执行该运行。
