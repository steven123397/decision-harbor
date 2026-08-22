# 01 — 异步提交与单 Worker 执行的最小闭环

**What to build:** 分析用户提交合法 SQL 后立即得到 HTTP 202 与查询运行事实；策略拒绝在提交阶段立即返回 HTTP 422 与 `rejected` 运行。独立 Worker 从 platform PostgreSQL 持久队列领取 `queued` 运行，用 analytics 只读身份执行，成功时在同一事务原子发布 `succeeded` 终态与结果快照，失败时以条件更新发布 `failed`。用户通过 `GET /api/v1/query-runs/{id}` 恢复状态，通过 `GET /api/v1/query-runs/{id}/result` 读取成功结果；Web 工作台提交后轮询到终态再渲染结果，刷新后仍可按运行标识恢复。本票是贯穿迁移、API、Worker、Web 与测试的 tracer bullet，并吸收同步实现的迁移 prefactor：API 不再执行用户 SQL。

依据 spec「Implementation Decisions」「External HTTP Contract」与 ADR-0003（PostgreSQL 持久队列）、ADR-0004（有限结果快照）、ADR-0006（有界游标提取）。快照在本票只需满足常规行数/字节边界；完整大小矩阵由 02 交付。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-agent

- [ ] `POST /api/v1/query-runs` 同步完成请求校验与 SQL AST 策略判定：允许时完成 `received → queued` 并返回 202 与运行事实；拒绝时完成 `received → rejected` 并返回 422 与原运行
- [ ] 策略治理边界与基线一致：AST 默认拒绝、对象允许范围、类型转换约束不因异步化扩大
- [ ] 迁移把状态集扩展为 spec 的 8 个状态，数据库约束继续绑定状态事实与字段的关联
- [ ] Worker 领取产生执行尝试记录（Worker 标识、generation、租约字段）；成功终态与结果快照在一个 platform 事务中原子保存；失败以条件更新发布并保留稳定错误码
- [ ] 用户 SQL 只在 Worker 内以 analytics 只读身份执行，继续使用服务端游标有界提取，成功、失败与失租路径都关闭游标并结束事务
- [ ] 全局并发上限默认 4，以数据库中有效执行所有权计数为准（本票单 Worker 场景下成立）
- [ ] `GET /api/v1/query-runs/{id}` 存在返回 200，不存在返回 404 `query_run_not_found`
- [ ] `GET /api/v1/query-runs/{id}/result`：快照可读返回 200；未完成返回 409 `result_not_ready`；终态无可读快照返回 409 `result_unavailable`；结果读取永远不重新执行 SQL
- [ ] API 进程只持有 platform 凭据与就绪探测凭据；Worker 持有 platform 任务凭据与 analytics 查询凭据；运行环境编排相应调整
- [ ] 同步时代遗留移除：API 内并发信号量、容量等待与启动批量失败逻辑删除；非法 Worker 配置（配置值非正整数，或心跳不严格小于租约）使对应进程启动非零退出
- [ ] Web 提交后轮询运行状态到终态并渲染结果，终态后停止轮询；桌面浏览器端到端路径可用
- [ ] `./dev test` 全绿（API 单元/集成、Web、e2e 按新合同更新）
