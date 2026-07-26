# DecisionHarbor Agent 工作规则

本文件适用于整个仓库。子目录如有自己的 `AGENTS.md`（如 `docs/AGENTS.md`），在其范围内更近的规则优先。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 开发前阅读

开始实现前，先阅读：

- `README.md`
- `docs/background/product-requirements.md`
- `docs/background/technical-constraints.md`
- `datasets/sales-analytics-v1/README.md`
- `datasets/sales-analytics-v1/contract.json`
- `docs/status/project_status.md`
- 当前任务涉及的 `docs/design/` 与 `docs/plan/` 文档（经 `docs/index.md` 导航）

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档与工作区

- `docs/index.md` 是文档导航入口；目录职责与写作规则见 `docs/AGENTS.md`。
- `docs/background/` 保存已确认的产品背景、需求和技术约束；不要改写既有背景约束来迁就实现。
- 实现过程产生的产品内生文档写入 `docs/design/`（长期设计）、`docs/plan/`（阶段计划）或 `docs/status/`（当前事实），并登记进 `docs/index.md`。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## Git 与验证

- 提交信息遵循 `type(范围): 中文描述`，如 `feat(项目): 建立初始产品背景与固定数据`；仅在用户要求时提交或推送。
- 修改 `datasets/sales-analytics-v1/` 后必须在该目录运行 `python3 validate.py` 并通过。
- 应用建立后，以项目自身文档和依赖清单记录的统一构建、测试命令为验证标准。
- 不提交依赖安装物、构建产物、缓存或本地运行数据。
