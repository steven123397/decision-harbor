# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

当前仓库提供首轮产品背景、需求、技术约束、固定销售分析数据和可运行的 Web/API/PostgreSQL 基座。

## 项目文档

- [文档入口](docs/index.md)：查看默认阅读顺序和各类文档职责。

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

## 本地运行

需要 Docker、Docker Compose v2、Node.js 和 Python 3.13。首次启动前可复制 `.env.example` 为 `.env`，再按需修改当前实例的项目名和 Web/API 宿主端口：

```bash
cp .env.example .env
./dev up
```

启动后访问 `http://127.0.0.1:15173`；API 的 `/health` 和 `/ready` 也可直接访问。`./dev down` 只停止当前 Compose 项目，`./dev destroy` 才会连同当前项目数据卷一起删除。

验证数据、API、Web 单元测试和浏览器主链：

```bash
./dev test
```
