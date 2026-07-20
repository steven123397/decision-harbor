# DecisionHarbor Agent 工作规则

## 规则作用域

- 本文件适用于整个仓库。更深目录中的 `AGENTS.md` 只补充该子树的特殊规则；发生冲突时，更近的规则优先，其余根规则继续生效。
- 在用户指定的工作树内操作。未经明确要求，不读取或修改其他工作树。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 默认阅读顺序

开始工作前，按以下顺序恢复上下文：

- `README.md`
- `docs/index.md`
- `docs/background/product-requirements.md`
- `docs/background/technical-constraints.md`
- `datasets/sales-analytics-v1/README.md`
- `datasets/sales-analytics-v1/contract.json`
- `docs/status/project_status.md`
- 与当前任务直接相关的 `docs/design/` 和 `docs/plan/` 文档

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档职责

- `docs/background/` 保存外部输入、已确认需求和技术约束，不为迁就实现而改写。
- `docs/design/` 保存长期有效的产品内生设计，包括领域模型、接口、数据边界和关键决策。
- `docs/plan/` 保存阶段目标、任务切片、依赖、验证方式和交付边界，不代替设计文档。
- `docs/status/` 保存当前结论、进展、风险和下一步，不承载长期规则或未来设计。
- `docs/index.md` 只提供正式文档导航和阅读入口，不复制各文档正文。

新增或移动正式文档时同步更新 `docs/index.md`。变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的设计、计划、状态文档与测试。

## 事实来源

- 产品范围与技术约束以 `docs/background/` 为准；已经确认的产品内生决策写入 `docs/design/`。
- `datasets/sales-analytics-v1/contract.json` 与已提交 CSV 是数据结构、行数和业务口径的权威来源。
- `docs/plan/` 说明如何交付，`docs/status/` 说明当前进展；二者不得重新定义背景约束或已确认设计。

## Git、验证与产物

- 开始修改前运行 `git status --short --branch`，保护已有改动。未经用户明确要求，不回滚、提交或推送变更。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 当前仓库可从 `datasets/sales-analytics-v1/` 运行 `python3 validate.py` 校验固定数据集。应用工具链建立后，以相应依赖清单和正式文档中的命令为准。
- 完成修改前运行与变更最接近的验证命令，并运行 `git diff --check`。无法运行的验证必须如实说明。
- 不提交 `.env`、依赖目录、缓存、构建产物或测试报告；具体边界以 `.gitignore` 为准。
