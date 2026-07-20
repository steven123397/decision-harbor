# 项目状态

## 当前结论

- 首轮目标已实现：Compose 可启动 Web + API + PostgreSQL；AST/对象范围治理的只读 SQL；查询审计；最小工作台；固定数据迁移与幂等 seed；可并行 Compose；规定 HTTP 接口与分层测试。
- 正式设计见 `docs/design/first-round-system.md`；实现计划见 `docs/plan/first-round-implementation.md`。
- 无终端用户鉴权，仅可信本地环境可用。

## 进展

- [x] 根规则、背景、数据集、文档治理
- [x] 首轮系统设计与领域术语
- [x] 首轮实现计划
- [x] 应用骨架（`apps/api`、`apps/web`、Compose）
- [x] 受治理 SQL 查询执行链路与审计
- [x] 最小查询工作台
- [x] 统一本地启动与分层测试（`scripts/up.sh`、`scripts/test.sh`）

## 风险与阻塞

- Docker Hub / PyPI 拉取偶发缓慢或 EOF，可能拉长首次构建时间（不阻塞功能正确性）。
- 宿主为 Python 3.10 时，以 API 容器内 Python 3.13 为权威运行与测试环境。
- Playwright 需本机安装 Chromium 浏览器二进制（`npx playwright install chromium`）。

## 下一步

1. 按需提交本 worktree 变更（当前默认未 commit）。
2. 若部署出本地可信环境，补终端用户鉴权（设计延后项）。
3. 长查询场景再评估异步执行模型。

## 最近验证

| 检查 | 结果 |
| --- | --- |
| `git diff --check` | 通过 |
| `python3 datasets/sales-analytics-v1/validate.py` | 通过 |
| Compose `./scripts/up.sh` | 通过（`/health` ok，`/ready` ready） |
| `POST` 允许查询 | `status=succeeded`，区域聚合 5 行 |
| `POST` 拒绝 `DELETE` | `status=rejected`，`POLICY_FORBIDDEN_STATEMENT` |
| API `pytest`（unit+integration） | 26 passed |
| Playwright 工作台主链 | 2 passed |
