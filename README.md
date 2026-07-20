# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

首轮交付包含可运行的 Web、API 与 PostgreSQL 基座，以及固定销售分析数据集的迁移与幂等 seed。

> **安全提示：** 首轮无终端用户鉴权，仅适用于可信本地环境；请勿将服务直接暴露到公网。

## 文档

- [文档索引](docs/index.md) — 正式文档导航与阅读入口
- [项目状态](docs/status/project_status.md) — 当前事实与下一步
- [首轮系统设计](docs/design/first-round-system.md)
- [实现计划](docs/plan/first-round-implementation.md)
- [产品需求](docs/background/product-requirements.md)
- [技术约束](docs/background/technical-constraints.md)
- [销售分析数据集 v1](datasets/sales-analytics-v1/README.md)

## 快速启动

前置：Git、Docker、Docker Compose。

```bash
cp .env.example .env   # 可按工作区修改 COMPOSE_PROJECT_NAME / 端口
./scripts/up.sh        # 构建、启动、等待 /ready
```

- Web 工作台：`http://127.0.0.1:${WEB_HOST_PORT:-5173}`
- API：`http://127.0.0.1:${API_HOST_PORT:-8000}`
- 健康检查：`GET /health`、就绪检查：`GET /ready`

停止：

```bash
./scripts/down.sh
```

多工作区并行时，为每个工作区设置不同的 `COMPOSE_PROJECT_NAME`、`API_HOST_PORT`、`WEB_HOST_PORT`。Compose 不使用固定 `container_name`，数据库默认不映射宿主端口。

## 测试

```bash
./scripts/test.sh
```

覆盖：数据集 `validate.py`、SqlPolicy 单元测试、双库集成测试、Playwright 工作台主流程。

仅校验公开数据集：

```bash
python3 datasets/sales-analytics-v1/validate.py
```

## 固定数据集

`datasets/sales-analytics-v1/` 包含可重复生成并校验的公开合成销售数据。其 `contract.json` 定义五张分析表、字段、关联和业务口径；已提交的 CSV 是当前版本的权威数据。

## 仓库结构（首轮）

| 路径 | 说明 |
| --- | --- |
| `apps/api` | FastAPI、SQL 策略、执行器、审计、迁移与 seed |
| `apps/web` | React 最小查询工作台 |
| `datasets/sales-analytics-v1` | 固定分析数据契约与 fixture |
| `docs/` | 背景 / 设计 / 计划 / 状态 |
| `scripts/` | 统一启动与测试入口 |
| `docker-compose.yml` | 本地三服务编排 |
