# DecisionHarbor Agent 工作规则

## 适用范围与优先级

本文件是仓库根规则。更近的规则优先：子树 `AGENTS.md`（如 `docs/AGENTS.md`）在其目录范围内覆盖根规则；任务说明覆盖两者。

以下输入是产品事实来源，不得为实现迁就而改名、删除或重新解释：

- `docs/background/` 中已确认的产品背景、需求与技术约束；
- `datasets/sales-analytics-v1/` 的数据契约、公开 CSV、生成器、校验器和 manifest。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 安全与访问边界（长期不变量）

- 用户 SQL 只在 `analytics` 数据库上通过独立只读身份执行，这是应用层策略之外的第二道边界。
- `platform` 数据库保存查询审计等产品状态，只允许平台可写身份访问。
- SQL 治理必须基于 AST 与对象访问范围实施，不得只依赖字符串黑名单。
- 细节以 `docs/background/product-requirements.md` 为准。

## 默认阅读顺序

新 Agent 恢复项目状态时，按序阅读：

1. `README.md` —— 项目与人阅读入口。
2. `docs/index.md` —— 文档导航与目录职责。
3. `docs/background/product-requirements.md`
4. `docs/background/technical-constraints.md`
5. `datasets/sales-analytics-v1/README.md`
6. `datasets/sales-analytics-v1/contract.json`
7. 按当前任务需要，阅读 `docs/design/`、`docs/plan/`、`docs/status/`。

## 文档目录职责

- `docs/background/`：已确认的外部输入、需求背景与技术约束来源，只读。
- `docs/design/`：长期边界、领域模型、接口、数据与关键决策。
- `docs/plan/`：阶段目标、任务切片、依赖、验证与交付边界。
- `docs/status/`：当前事实、进展、风险与下一步。
- `docs/index.md`：只负责导航与阅读入口，不复制正文。

同一事实只保留在一处，不跨目录双写；实现产生的新文档遵循上述职责，不改写背景约束来迁就实现。

## Git、验证与提交约束

- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪（已在 `.gitignore` 中忽略）。
- 数据集校验：在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py`。
- 应用级构建、测试与运行命令以 `docs/status/project_status.md` 登记的实际命令为准。
- 提交前运行 `git diff --check`；不提交依赖目录、构建产物与本地环境文件（见 `.gitignore`）。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## 缓存、依赖与构建产物边界

- 依赖目录（`node_modules/`、`.venv/`）、构建产物（`dist/`、`__pycache__/`、`.pytest_cache/`、`playwright-report/`、`test-results/`）与本地环境文件（`.env` 系列）不入库。
- `.env.example` 可入库，用于声明可配置项。
