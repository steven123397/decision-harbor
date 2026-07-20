# 首轮实现计划

## 阶段目标

交付 `docs/design/` 五篇定义的首轮系统：可运行的 Web + API + PostgreSQL 基座、AST 治理的只读 SQL 执行、查询审计、最小工作台、幂等迁移与 seed、可并行 Compose，以及三层测试。

## 交付边界

包含：`api/`（FastAPI、策略、执行器、审计、迁移、seed）、`web/`（工作台、nginx）、`db/`（初始化脚本）、`docker-compose.yml`、`.env.example`、`scripts/`（统一运行与测试命令）、三层测试。不包含：背景非目标与设计延后决策中的任何项。

## 任务切片与依赖

1. `api/` 骨架（依赖清单、配置、双库连接、审计模型）——无依赖。
2. 策略模块 TDD：pytest 失败矩阵 → SQLGlot 实现 → 通过——依赖 1。
3. 执行器 + 审计仓储 + 路由与错误信封——依赖 2。
4. Alembic 双库迁移 + 授权 + 幂等 seed——依赖 1。
5. `db/` init、Compose、`.env.example`、`scripts/run.sh`、`scripts/test.sh`——依赖 3、4。
6. `web/` 工作台 + nginx + Vitest——依赖 3 的 API 契约。
7. 集成测试（Compose 内）+ Playwright 主链——依赖 5、6。
8. 全量验证与证据记录——依赖 7。

## 验证

- 机械验证：`git diff --check`；数据集 `python3 validate.py`。
- 行为验证：`scripts/test.sh`（策略单元、双库集成、Vitest、Playwright）；`scripts/run.sh` 启动后 `/health`、`/ready`、允许与拒绝查询的 API 证据。
- 共识验证：无（本阶段无需求方评审环节）。

## 未规划区

异步执行、结果持久化、认证、函数级限制、性能压测（设计延后决策）。
