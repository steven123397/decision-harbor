# DecisionHarbor Agent 工作规则

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

`docs/` 的完整导航见 `docs/index.md`。

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档与工作区

- `docs/` 的阅读入口是 `docs/index.md`，目录职责分为 `background/`（已确认的外部输入）、`design/`（长期设计决策）、`plan/`（阶段计划）和 `status/`（当前状态）。
- 文档写入与维护规则见 `docs/AGENTS.md`；在 `docs/` 范围内，该文件优先于本文件。
- 实现过程产生的产品内生文档应遵循上述结构；不要改写既有背景约束来迁就实现。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。
