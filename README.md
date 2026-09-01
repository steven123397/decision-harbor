# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

当前仓库实现受治理 SQL 查询链路，包括 React 查询工作台、FastAPI、独立 Worker、两个 PostgreSQL 逻辑数据库、SQLGlot AST 策略、持久查询队列与执行租约、有界结果快照、查询审计、迁移、固定数据 seed 和容器化测试。提交后查询异步执行：API 同步完成策略判定并入队，Worker 领取并以 analytics 只读身份执行，客户端轮询读取状态与结果。

协作入口见 [Agent 工作规则](AGENTS.md)，规范术语见 [领域上下文](CONTEXT.md)。长期技术取舍记录在 [ADR](docs/adr/README.md)，当前功能规格和 tickets 记录在 `.scratch/`。

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

一次 `./dev up` 启动三个应用进程和一个 PostgreSQL 实例（其中包含 platform 与 analytics 两个逻辑数据库）：Web 工作台（反向代理 API）、API（策略判定、入队与运行状态）和 Worker（领取执行查询、维护租约与结果清理，就绪探测在容器内 8001 端口）。`init` 一次性完成角色、数据库、授权、迁移与固定数据 seed 后退出；重复执行 seed 为无操作，检测到未知状态时按 [ADR-0008](docs/adr/0008-fail-closed-when-seeding-fixed-data.md) 以 `seed_conflict` 失败而不覆盖数据。

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

提交一条受治理查询：策略允许时返回 HTTP 202 和 `queued` 查询运行，策略拒绝返回 HTTP 422 和 `rejected` 运行：

```bash
curl -H 'content-type: application/json' \
  --data '{"sql":"SELECT region, count(*) FROM customers GROUP BY region"}' \
  http://127.0.0.1:8000/api/v1/query-runs
```

轮询查询运行状态：

```bash
curl http://127.0.0.1:8000/api/v1/query-runs/<query-run-id>
```

读取成功运行的有限结果快照（未完成返回 409 `result_not_ready`，终态无可读快照返回 409 `result_unavailable`）：

```bash
curl http://127.0.0.1:8000/api/v1/query-runs/<query-run-id>/result
```

用户 SQL 只由独立 Worker 以 analytics 只读身份执行；API 进程不持有 analytics 查询凭据。结果读取永远不重新执行 SQL。

## 测试

统一入口会启动完整 Compose 环境，并在容器内运行 pytest、Vitest 和 Playwright：

```bash
./dev test
```

首轮实现的历史设计、计划和状态保存在 [历史文档](docs/archive/README.md)。v0.2.0 的异步查询生命周期以 [当前规格](.scratch/decisionharbor-v0.2.0/spec.md) 为准。
