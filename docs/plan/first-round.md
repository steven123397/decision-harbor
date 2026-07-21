# 首轮实现计划

依据 [`design/first-round.md`](../design/first-round.md) 与 [`background/`](../background/) 实现 DecisionHarbor 首轮。设计与背景冲突时停下说明，不降低约束。本计划为最小可执行切片，按依赖顺序推进，不停在计划阶段。

## 约束与不变量（来自背景，不可降低）

- 五表字段、业务口径、定点数、`discount_rate` 0–1、订单总额不冗余，严格遵循 `datasets/sales-analytics-v1/contract.json`。
- 双库 + 独立只读/写入身份为第二道边界；用户 SQL 只在 `analytics` 只读身份执行。
- 策略基于 SQLGlot AST 与对象范围，fail-closed；不依赖字符串黑名单。
- 资源限制：语句超时 + 行数上限（超限 fail-closed，不截断）。
- Compose 并行隔离：项目名/端口可配置，无固定 container_name/网络/卷/绑定目录，DB 不暴露固定宿主端口。
- 统一命令完成构建→启动→建库建角色→迁移+seed→等待 health/ready；重复非破坏。

## 切片（依赖顺序）

1. **基座**：`.env.example`、`Makefile`、`docker-compose.yml`、`db/init/` 建双库与角色。
2. **数据层**：analytics 幂等 seed 脚本（从契约+CSV）；platform Alembic 迁移建 `query_runs` 并授权。
3. **策略模块**：`api/app/policy.py` SQLGlot `analyze` 纯函数 + `tests/test_policy.py` 单元测试（先补失败测试再实现）。
4. **执行器与审计**：`executor.py` 只读身份+资源限制；`audit.py`/`models.py` platform 仓储。
5. **API**：`main.py` 端点、`schemas.py` 统一响应、错误语义。
6. **集成测试**：`tests/test_integration.py` 身份分离 + 端到端 + 迁移/seed 幂等。
7. **Web 工作台**：React+Vite+TS 最小工作台 + Playwright 主流程。
8. **编排与命令**：API entrypoint（migrate+seed+uvicorn）、health/ready、`make up/down/test`。
9. **验证**：dataset 校验、单元/集成测试、compose 启动、`/health`、`/ready`、允许/拒绝查询 API 证据、浏览器主链；环境缺失如实说明。

## 验证（机械/行为/共识）

- 机械：`git diff --check`、`python3 validate.py`、镜像构建、compose 启动、迁移/seed 重复收敛。
- 行为：策略单元（允许/拒绝/对象范围/边界）、双库身份分离集成、端到端允许/拒绝/失败、浏览器允许/拒绝/多语句主流程。
- 共识：设计与背景一致性已核对；实现不改写契约与背景。

## 交付边界

- 可运行 Web/API/PG 基座；受治理只读 SQL 执行；查询审计；最小工作台；固定数据迁移与幂等 seed；可并行 Compose；HTTP 接口与三层测试。
- 不含：认证/RBAC、异步队列、结果持久化、NL2SQL/LLM 等（设计延后项）。
