# 项目状态

更新日期：2026-07-27

## 当前结论

- 首轮受治理 SQL 查询链路已实现，可通过 `./dev up` 启动 Web、API、PostgreSQL 18 和一次性初始化服务。
- SQLGlot AST 的对象、函数与 CAST/DataType 允许模型，以及分析查询身份和显式只读事务共同约束用户 SQL；对象标识类型不能在无表引用时解析 relation、role、function、namespace 或 type catalog。
- 审计中的 `referenced_objects` 仅记录允许的契约业务表；允许的空数组只对应没有解析数据库对象的常量表达式。查询审计先于执行创建，终态持久化后才返回结果。
- `platform` 与 `analytics` 使用独立运行时身份、迁移链和连接；分析查询身份只能读取业务表，独立分析就绪身份只能读取迁移版本和 seed 标记。固定数据可重复 seed，匹配时无操作，冲突时失败。
- 正式文档已按 background、design、plan 与 status 分工，并由 `docs/index.md` 统一导航。
- 背景资料、数据 contract、公开 CSV、生成器、校验器和 manifest 未改写。

## 进展

- React 工作台已覆盖初始、运行中、成功、拒绝、失败、截断、未就绪和全部已知 API 错误码的稳定文案，桌面与移动视口无已知重叠。
- FastAPI 已提供 `/health`、`/ready`、`POST /api/v1/query-runs` 和 `GET /api/v1/query-runs/{id}`，统一使用稳定 envelope 与错误码。数据库不可达或依赖探测阻塞时，`/ready` 在 1 秒截止时间内返回 `service_not_ready`。
- 两套 Alembic 迁移、最小授权、启动恢复、资源限制、有界结果提取和结果类型序列化已落地；分析迁移 head 为 `analytics_0002`。
- 2026-07-27 的 `WEB_HOST_PORT=15173 API_HOST_PORT=18080 ./dev test` 证据为 pytest 87 个、Vitest 26 个、Playwright 4 个全部通过；npm audit 未在本次验证中重新运行。
- 两个 Compose 项目以不同端口同时就绪，使用不同网络和数据卷；第一实例已有审计记录时，第二实例仍为 0。
- seed 自动化覆盖空库首次载入、重复无操作、摘要冲突和无标记已有行冲突，失败路径保持既有数据不变。

## 风险

- 首轮无应用鉴权，只允许本机受信任环境；共享网络或生产部署前必须另行设计鉴权、主体审计、TLS 和部署边界。
- 查询容量限制只在单个 API 进程内生效，启动恢复也基于单实例前提；多 API 实例需要重新设计所有权和协调。
- 查询结果不持久化，页面刷新和 GET 只能恢复审计事实；异步执行、取消和历史结果均不在首轮范围。
- 函数与类型允许集有意保守；新增 PostgreSQL 函数、类型或转换语法前，必须先扩展设计并补充 AST 与真实 PostgreSQL 证据。
- FastAPI TestClient 当前产生一条上游弃用提示，不影响测试结果，但依赖升级时应跟踪 Starlette 的 httpx2 迁移。

## 下一步

- 对首轮实现进行合并前审查；任何范围扩展先更新正式 design，再建立新的执行计划。
