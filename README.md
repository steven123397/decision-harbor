# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

仓库包含首轮实现：`api/`（FastAPI、SQLGlot 策略、执行器、审计、Alembic 迁移与 seed）、`web/`（React 查询工作台）、`db/`（初始化脚本）、`e2e/`（Playwright 浏览器测试）与 Docker Compose 本地运行环境。

## 本地运行

前置要求：Git、Docker、Docker Compose。

```bash
scripts/run.sh   # 构建并启动全栈，执行迁移与 seed，等待 /health 与 /ready
scripts/test.sh  # 策略单元、双库集成、前端单元与浏览器主流程测试
```

首次运行会从 `.env.example` 创建 `.env`。多个工作区并行运行时，在各自的 `.env` 中修改 `DH_PROJECT_NAME`、`DH_WEB_PORT` 与 `DH_API_PORT`。

启动后访问：工作台 `http://localhost:5173`，API `http://localhost:8000`（端口以 `.env` 为准）。

## 文档

[文档索引](docs/index.md) 是项目正式文档的导航入口，按背景、设计、计划、状态分目录维护。

## 背景资料

- [产品需求](docs/background/product-requirements.md)
- [技术约束](docs/background/technical-constraints.md)
- [销售分析数据集 v1](datasets/sales-analytics-v1/README.md)

## 固定数据集

`datasets/sales-analytics-v1/` 包含可重复生成并校验的公开合成销售数据。其 `contract.json` 定义五张分析表、字段、关联和业务口径；已提交的 CSV 是当前版本的权威数据。

在该目录运行以下命令可校验数据完整性：

```bash
python3 validate.py
```
