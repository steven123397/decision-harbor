# 项目状态

最后更新：首轮实现完成时。

## 当前状态

DecisionHarbor 首轮目标已完成并验证通过：可运行的 Web/API/PostgreSQL 基座、AST+对象范围治理的只读 SQL 执行、查询审计、最小工作台、固定数据迁移与幂等 seed、可并行 Compose 配置、HTTP 接口与三层测试全部就绪。

## 进展

- `api/`：FastAPI + SQLGlot 策略 + SQLAlchemy/psycopg 执行器 + Alembic 平台迁移 + 幂等 seed（contract 驱动）；/health、/ready、query-runs 接口。
- `web/`：React 19 + Vite 最小查询工作台（输入/提交/轮询/结果表/拒绝展示）。
- `docker-compose.yml` + `Makefile`：并行隔离（COMPOSE_PROJECT_NAME、API_PORT、WEB_PORT 可配置，DB 不暴露宿主端口）；统一命令 `make up` / `make test`。
- `docs/`：背景、设计、计划、状态齐全。

## 验证命令（仓库真实工具链）

- 数据集校验：`cd datasets/sales-analytics-v1 && python3 validate.py`。
- 统一测试：`make test`（SQL 策略单测 36、双库集成 14、工作台 vitest 1、浏览器主流程 2）。
- 统一启动：`make up`，就绪探针 `/ready`。
- 提交前：`git diff --check`。

## 风险

- Web↔API 跨端口经 CORS 直连，API 宿主端口需与 Web 构建时的 VITE_API_BASE_URL 一致（`.env` 已固化，改端口需重建 web 镜像）。
- 结果行仅内存缓存，API 重启后结果不可取（审计元数据仍在），属设计第 9 节延后项。

## 下一步

- 进入后续阶段前，按 `docs/design/system-design.md` 第 9 节延后决策评估 auth、结果持久化、取消、多实例队列等。
