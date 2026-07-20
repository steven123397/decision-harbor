# 首轮实现计划

## 1. 目标与完成定义

交付可运行的 DecisionHarbor 首轮基座：Compose 启动 Web + API + PostgreSQL；AST/对象范围治理的只读 SQL；查询审计；最小工作台；固定数据迁移与幂等 seed；可并行 Compose；设计规定的 HTTP 接口与分层测试。

完成定义见 `docs/design/first-round-system.md` 目标 1–6 与验证矩阵。

## 2. 范围与停止线

- **包含：** `apps/api`、`apps/web`、Compose、迁移/seed、策略/执行/审计、统一启动与测试脚本、文档索引/状态同步。
- **不包含：** LLM/NL2SQL、鉴权 RBAC、异步队列、结果网格持久化、图表编辑器。
- **冲突处理：** 设计与背景冲突时停止，不降约束。

## 3. 前置条件

- 设计：`docs/design/first-round-system.md`、`CONTEXT.md`
- 背景与契约已固定
- 工具：Docker Compose、Node 24（宿主已有）、Python 3.13 优先在 API 容器内

## 4. 任务清单

| ID | 任务 | 依赖 | 验证 |
| --- | --- | --- | --- |
| T1 | SqlPolicy 单元测试 + 实现 | — | pytest unit |
| T2 | platform/analytics 迁移、幂等 seed、角色初始化 | — | 重复 seed 行数正确 |
| T3 | QueryRun 仓储/执行器/服务 + HTTP 四端点 | T1,T2 | API 允许/拒绝 |
| T4 | Compose 并行隔离与统一启动 | T2,T3 | `/health` `/ready` |
| T5 | 最小查询工作台 | T3 | 手工/Playwright |
| T6 | 双库集成测试 + Playwright + `scripts/test.sh` | T1–T5 | 统一测试命令 |

## 5. 验证矩阵

| 证据 | 命令/动作 |
| --- | --- |
| 机械 | `git diff --check`；`datasets/.../validate.py`；`scripts/test.sh` |
| 行为 | Compose up；curl health/ready/query；Playwright 主链 |
| 共识 | 实现不改写 background/contract 口径 |

## 6. 文档同步

- 完成后更新 `docs/status/project_status.md`、`docs/index.md`、根 `README.md`（运行方式）
- 不把进度写入根 `AGENTS.md`
