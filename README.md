# DecisionHarbor

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。它让用户提交显式 SQL，并在受控规则内完成校验、只读执行、结果展示与查询审计。

当前仓库提供首轮可运行的受治理 SQL 查询链路：Web 查询工作台、FastAPI 查询服务、PostgreSQL 双数据库（审计与固定分析数据），以及 Docker Compose 本地运行与测试命令。

## 架构一览

- `web/`：React 19 + TypeScript + Vite 查询工作台，轮询驱动异步状态推进（已受理/排队/执行中/取消中中间态、结果快照与截断标记、拒绝与失败面板）；Nginx 托管静态资源并同源反向代理 API（无 CORS）。
- `api/`：Python 3.13 + FastAPI。`app/policy/` 是 SQL 策略判定（纯函数、全维度白名单）；`app/runs/` 受理提交（同事务入队或拒绝，支持幂等键重放）与历史分页、运行队列网关（全局并发闸门、attempt 硬上界 3、基础设施类失败自动重跑，ADR-0019）与取消/重试操作（排队取消确定生效、运行中 best effort 中止、retry_of 关系，ADR-0018）；结果快照自终态发布起保留 24 小时，`GET result` 携带 `expires_at`，过期 410、审计行仍可读，超期快照由幂等清理整行删除；`app/worker.py` 是后台执行组件（只读身份执行、终态+快照原子发布；×2 实例 SKIP LOCKED 协同消费队列、租约心跳续期、过期接管与 generation fencing，ADR-0017；keeper 周期清理超期快照）；`app/seed/` 从契约派生 DDL 并加载固定数据；Alembic 迁移管理 `platform` 库。
- `deploy/`：Compose 栈（web、api、worker ×2、一次性 init 容器、PostgreSQL 与 `test` profile 测试服务）。init 容器以 owner 身份完成迁移与幂等 seed 后退出，api 与 worker 只持低权限凭据。
- `datasets/sales-analytics-v1/`：产品输入，`contract.json` 是五张分析表的单一事实源。

决策与理由见 [docs/adr/](docs/adr/)；领域术语见 [CONTEXT.md](CONTEXT.md)。

## 快速开始

```bash
cp .env.example .env
make up      # 构建、启动、迁移、seed，等待健康与就绪
make test    # 单元、集成、Web 与浏览器测试
```

Web 工作台：<http://localhost:8080>；API：<http://localhost:8081>。浏览器测试首次需在 `web/` 下 `npm install` 并 `npx playwright install chromium`。并行工作区经 `.env` 的 `COMPOSE_PROJECT_NAME`、`WEB_PORT`、`API_PORT` 隔离。

## 验证

- `make up` 幂等：重复启动与 restart 后行数不变（100/8/50/1000/3000）、seed 标记不变、审计记录持久。
- `make test` 分层：策略与 API 单元测试（api 容器）→ 双数据库集成测试（一次性 `api-test` 容器）→ Web 单元测试（`web-test`）→ Playwright 浏览器主流程 / 拒绝 / 失败 / 排队与尝试推进 / 键盘与窄视口。
- `make dataset-validate` 校验固定数据集完整性（等价于在 `datasets/sales-analytics-v1/` 下运行 `python3 validate.py`）。
- 治理绕过回归：CTE 遮蔽、`::regclass`、`query_to_xml`、`current_user` 在单元与集成两层锁定拒绝。

## 文档

- [CONTEXT.md](CONTEXT.md) — 领域词汇表
- [docs/adr/](docs/adr/) — 决策记录
- [产品需求](docs/background/product-requirements.md) / [技术约束](docs/background/technical-constraints.md) — 固定的外部输入
- [销售分析数据集 v1](datasets/sales-analytics-v1/README.md)

## 固定数据集

`datasets/sales-analytics-v1/` 包含可重复生成并校验的公开合成销售数据。其 `contract.json` 定义五张分析表、字段、关联和业务口径；已提交的 CSV 是当前版本的权威数据。
