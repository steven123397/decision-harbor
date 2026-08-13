# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

在只安装 Git、Docker 和 Docker Compose 的环境中，可用统一命令启动 Web、API 与 PostgreSQL，并运行首轮测试。

## 文档入口

正式文档从 [docs/index.md](docs/index.md) 进入。`background` 保存已确认的外部输入，`design`、`plan` 与 `status` 分别承担长期决策、阶段计划和当前事实。

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

复制 `.env.example` 为 `.env`，按工作区修改 `COMPOSE_PROJECT_NAME`、`WEB_HOST_PORT` 和 `API_HOST_PORT`，然后启动：

```bash
./scripts/up.sh
```

浏览器打开 Web 宿主端口即可使用查询工作台。API 宿主端口供调试直连；工作台通过 Web 同源反代访问 API。

## 测试

```bash
./scripts/test.sh
```

该命令在同一 Compose 项目中运行 SQL 策略与 HTTP 单元测试、双数据库集成测试、工作台 Vitest 和 Playwright 主流程。
