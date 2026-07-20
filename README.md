# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

当前仓库已实现首轮受治理 SQL 查询链路，包括 React 查询工作台、FastAPI、两个 PostgreSQL 逻辑数据库、SQLGlot AST 策略、查询审计、迁移、固定数据 seed 和容器化测试。

项目文档的职责、阅读顺序和正式入口见 [文档索引](docs/index.md)。

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

唯一运行前提是 Docker 与 Docker Compose。应用的 Python 3.13、Node.js 24 和 PostgreSQL 18 均在容器内运行。

```bash
./dev up
```

启动完成后访问：

- 查询工作台：`http://127.0.0.1:5173`
- API 健康检查：`http://127.0.0.1:8000/health`
- API 就绪检查：`http://127.0.0.1:8000/ready`

默认宿主地址只绑定回环接口。首轮没有应用鉴权，不得直接用于共享网络或生产部署。

停止服务但保留当前实例的数据卷：

```bash
./dev down
```

只有明确需要删除当前实例全部数据库数据时才运行：

```bash
./dev destroy
```

## 并行实例

项目名和两个宿主端口都可以覆盖，因此不同工作区可以同时运行且不会共享网络或 PostgreSQL 卷：

```bash
COMPOSE_PROJECT_NAME=decisionharbor-a \
WEB_HOST_PORT=15173 \
API_HOST_PORT=18080 \
./dev up
```

## 查询 API

提交一条同步只读查询：

```bash
curl -H 'content-type: application/json' \
  --data '{"sql":"SELECT region, count(*) FROM customers GROUP BY region"}' \
  http://127.0.0.1:8000/api/v1/query-runs
```

读取持久化审计事实：

```bash
curl http://127.0.0.1:8000/api/v1/query-runs/<query-run-id>
```

历史读取不返回结果单元格；结果只随成功的 POST 即时返回。

## 测试

统一入口会启动完整 Compose 环境，并在容器内运行 pytest、Vitest 和 Playwright：

```bash
./dev test
```

实现边界与错误语义见[首轮系统设计](docs/design/first-release-system-design.md)，当前验证状态见[项目状态](docs/status/project_status.md)。
