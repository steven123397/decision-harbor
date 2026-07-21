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

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档与工作区

- `docs/background/` 保存已确认的产品背景、需求和技术约束。
- 实现过程产生的产品内生文档应遵循后续建立的项目文档结构；不要改写既有背景约束来迁就实现。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

### 文档目录职责

| 目录 | 职责 | 写入时机 |
| --- | --- | --- |
| `docs/background/` | 外部输入：产品需求、技术约束、数据契约说明 | 需求确认时，不由实现改写 |
| `docs/design/` | 长期边界：领域模型、接口、数据结构、关键决策 | 设计收敛后 |
| `docs/plan/` | 阶段目标、任务切片、依赖与验证标准 | 计划制定时 |
| `docs/status/` | 当前事实：进展、风险、下一步 | 阶段完成或状态变化时 |
| `docs/index.md` | 导航入口，只维护链接与阅读顺序，不复制正文 | 新增正式文档时同步 |

各目录的模板和写作规则见 `docs/AGENTS.md`。

### 默认阅读顺序

新 Agent 接手项目时按以下顺序阅读：

1. 本文件（`AGENTS.md`）
2. `README.md`
3. `docs/index.md`
4. `docs/background/product-requirements.md`
5. `docs/background/technical-constraints.md`
6. `datasets/sales-analytics-v1/README.md` 与 `contract.json`
7. `docs/status/project_status.md`（了解当前进展）
8. 与当前任务相关的 `docs/design/` 和 `docs/plan/` 文档
