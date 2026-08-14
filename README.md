# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

当前仓库提供首轮可运行的受治理 SQL 查询链路：Web 查询工作台、FastAPI 查询服务、PostgreSQL 双数据库（审计与固定分析数据），以及 Docker Compose 本地运行与测试命令。

## 快速开始

```bash
cp .env.example .env
make up      # 构建、启动、迁移、seed，等待健康与就绪
make test    # 单元、集成、Web 与浏览器测试
```

Web 工作台：<http://localhost:8080>；API：<http://localhost:8081>。浏览器测试首次需在 `web/` 下 `npm install` 并 `npx playwright install chromium`。

## 文档

- [文档索引](docs/index.md)
- [产品需求](docs/background/product-requirements.md)
- [技术约束](docs/background/technical-constraints.md)
- [销售分析数据集 v1](datasets/sales-analytics-v1/README.md)

## 固定数据集

`datasets/sales-analytics-v1/` 包含可重复生成并校验的公开合成销售数据。其 `contract.json` 定义五张分析表、字段、关联和业务口径；已提交的 CSV 是当前版本的权威数据。

在该目录运行以下命令可校验数据完整性：

```bash
python3 validate.py
```
