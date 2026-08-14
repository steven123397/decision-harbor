# 项目状态

本文档是 DecisionHarbor 的当前快照。它只记录已经可以从仓库和验证结果确认的结论，不替代设计文档或阶段计划。

## 当前结论

- 首轮显式 SQL 受治理查询链路已经实现：Web、FastAPI、PostgreSQL、AST 对象范围策略、只读执行、平台审计、固定数据迁移/seed 和 Compose 隔离均已建立。
- `platform` 与 `analytics` 是两个逻辑数据库；用户 SQL 只通过 `analytics_reader` 只读身份执行，审计只通过 `platform_writer` 写入，两个就绪检查使用独立只读身份。
- 背景资料、固定 CSV、`contract.json` 和 `docs/background/` 未被改写。

## 已完成进展

- SQLGlot PostgreSQL AST 策略覆盖单语句、只读根节点、写 CTE、对象白名单、函数/类型 allowlist、字节长度和字面量行数上限。
- Query Run Service 和 `platform.query_runs`/`platform.query_run_events` 实现 `received`、`executing`、`succeeded`、`rejected`、`failed` 状态以及稳定错误码；结果行不写入平台库。
- 两套 Alembic migration、双库角色初始化、固定 manifest/CSV seed 和“空库加载、完整库校验、冲突停止”规则已接入 Compose。
- React/Vite 查询工作台支持提交、执行中、成功、拒绝、失败、运行 ID、结果表和就绪状态；Nginx 代理 API 与就绪接口。
- `./dev` 提供 `up`、`down`、`destroy` 和 `test`，Compose 项目名、Web/API 宿主端口和数据卷均可实例级配置。

## 验证记录

2026-08-14 在 Compose 项目 `decisionharbor-codex-luna` 中完成：

- `python3 datasets/sales-analytics-v1/validate.py`：通过。
- `./dev test`：Python 3.13 容器内 `25 passed`，Web Vitest `2 passed`。
- `npm run test:browser`：Playwright 允许查询和对象越权拒绝 `2 passed`。
- `/health`：API 与 Web 代理均返回 `200 {"status":"ok"}`；`/ready`：API 与 Web 代理均返回 `200 {"status":"ready"}`。
- 真实允许查询返回 `succeeded`、5 行区域聚合结果；`SELECT * FROM platform.query_runs` 返回 `200` 业务拒绝，错误码为 `object_not_allowed`。
- 平台审计核对了成功运行的 `received -> executing -> succeeded` 和拒绝运行的 `received -> rejected` 事件；重复 seed 日志返回 `already_loaded`。
- `git diff --check` 已通过；当前分支没有新增 commit。

## 风险与验证边界

- 首轮同步请求不提供异步队列、取消或自动恢复；终态审计写入失败返回 `service_not_ready`，并保留未闭合审计事实，这是设计明确的首轮边界。
- 本机默认 Python 为 3.10，不能代表项目声明的 Python 3.13；正式 API 测试和 Compose 验证已在 Python 3.13 容器中完成。
- 浏览器测试依赖本机 Playwright Chromium；生产部署、认证/RBAC、备份恢复、结果持久化和更完整函数 allowlist 仍是非目标。

## 下一步

若继续扩展，应先在 `docs/design/` 记录身份认证、授权、异步运行或生产拓扑等新边界，再建立对应的 `docs/plan/`；当前首轮实现不自动扩张这些范围。
