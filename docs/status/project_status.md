# 项目状态

## 当前结论

- 治理骨架已建立：根 `AGENTS.md`、`docs/` 子树规则、数据集子树规则、文档索引与 `background`/`design`/`plan`/`status` 目录职责。
- 首轮设计已建立：[`design/first-round.md`](../design/first-round.md) 覆盖范围、模块与数据流、查询状态与审计、双库与只读身份边界、SQLGlot AST 策略与资源限制、最小 API 与错误语义、查询工作台、迁移与幂等 seed、Compose 并行隔离与测试接缝。
- 首轮实现已完成：Web/API/PostgreSQL 基座、受 AST 与对象范围治理的只读 SQL 执行、查询审计、最小工作台、固定数据迁移与幂等 seed、可并行 Compose 配置、HTTP 接口与三层测试。
- 固定数据集 `datasets/sales-analytics-v1/` 已提交并可作为产品输入。

## 进展

- 文档治理：完成最小骨架，区分 `background`（外部输入）、`design`（长期设计）、`plan`（阶段计划）、`status`（当前事实）。
- 设计：首轮权威设计完成，关键决策与理由已记录，未改写背景与契约。
- 实现：api（FastAPI + SQLGlot + SQLAlchemy + Alembic）、web（React 19 + Vite）、db（PostgreSQL 18 双逻辑库 + 双角色）、docker-compose、Makefile、.env.example。
- 验证：策略单元 36 passed；集成 14 passed（身份分离 + 端到端 + seed 幂等 + 计数匹配契约）；Playwright 3 passed（允许/拒绝/多语句主链）；API 证据 `/health` `/ready` 200、允许 200 succeeded、DELETE 422 rejected、多语句 422 rejected、超行数 200 failed ROW_LIMIT_EXCEEDED、GET 不返回结果行、404 正确；数据集校验通过；`git diff --check` 通过。

## 风险

- 首轮无认证/RBAC（设计延后项，非缺陷）。
- 仓库 API 镜像在慢网络下首次构建耗时较长（pip 下载）；宿主 venv（`api/.venv/`，未跟踪）用于加速本地测试，非运行路径。
- 结果行不持久化（设计决策）；如需历史结果回看需延后决策。

## 下一步

- 按用户指示 commit；或继续推进设计延后项（认证、异步队列、结果持久化等）。
