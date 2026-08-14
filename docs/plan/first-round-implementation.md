# 首轮实现计划

## 目标与交付边界

在不改变背景资料和数据契约的前提下，交付可运行的 Web、API、PostgreSQL 和 Compose 基座，以及受 AST 与对象范围治理的只读 SQL 查询链路、审计、固定数据迁移/重复 seed 和项目要求的测试覆盖。

本计划不包含 LLM、自然语言转 SQL、复杂 RBAC、异步队列、结果持久化、图表编辑器或生产部署。

## 实施顺序

1. **策略红绿切片：** 先建立 SQLGlot 单元测试和策略模块，覆盖允许查询、写操作、多语句、对象范围、函数/类型和资源边界。
2. **数据库基座切片：** 建立 Compose PostgreSQL、双数据库/身份初始化、两套 Alembic 迁移、固定 CSV seed 和真实权限/幂等集成测试。
3. **查询服务切片：** 实现审计存储、状态机、只读执行器、错误语义和 FastAPI `/health`、`/ready`、查询接口，并补服务与 API 测试。
4. **工作台切片：** 实现 React/Vite 查询工作台、响应状态映射和 Vitest 测试。
5. **集成验证切片：** 接入 Web/API/PostgreSQL Compose 主链，补 Playwright 浏览器测试、启动和重复 seed 证据。

## 验证门

- 每个行为切片先运行新失败测试，再实现并运行该切片测试和相邻回归。
- 数据库行为使用真实 PostgreSQL，不用内存数据库替代权限、事务、迁移和 seed 证据。
- 完成前运行数据集校验、项目测试、Compose 启动、`/health`、`/ready`、允许/拒绝查询 API 和浏览器主链。
- 文档与代码变更完成前运行 `git diff --check`；未授权不执行 `git add`、`git commit` 或 `git push`。

## 未规划区

认证授权、异步执行、查询保存与结果保留、生产部署、备份恢复和更完整的 PostgreSQL 函数 allowlist 留到后续需求确认后再设计。

## 完成记录

2026-08-14，本计划的五个切片均已落地并通过当前工作树验证：

- SQLGlot 策略、Query Run Service 和 HTTP API 已实现；策略单元、服务单元和 API 测试通过。
- 双数据库、四类运行时/就绪身份、Alembic 迁移和固定数据 seed 已在真实 PostgreSQL 18 容器中运行；首次 seed 返回 `loaded`，重复迁移/seed 返回 `already_loaded`。
- React/Vite 工作台已构建，Vitest 和 Playwright 主链通过。
- Compose 项目命名、端口和数据卷保持实例级隔离；`./dev test` 串行完成数据校验、Python 3.13 容器测试和 Web 单元测试。

阶段完成不表示非目标范围开放；后续变更仍需新增设计或阶段计划，并保持背景资料和数据契约不变。
