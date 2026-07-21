# DecisionHarbor Agent 工作规则

## 适用范围与优先级

- 本文件约束仓库根目录的长期协作规则。
- 更近的子树 `AGENTS.md`（如 `docs/AGENTS.md`、`datasets/sales-analytics-v1/AGENTS.md`）在其作用域内优先；冲突时以更近规则为准，但不得违背根规则中标注长期不变的边界。
- `.worktrees/` 是本地独立工作区，不参与本规则的事实来源。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 开发前阅读与阅读顺序

开始实现前，按以下顺序阅读：

1. `README.md` — 项目概述与文档入口
2. `docs/index.md` — 文档导航
3. `docs/background/product-requirements.md` — 产品需求
4. `docs/background/technical-constraints.md` — 技术约束
5. `datasets/sales-analytics-v1/README.md` 与 `contract.json` — 固定数据集
6. `docs/status/project_status.md` — 当前事实与下一步
7. `docs/design/` 下与当前任务相关的设计文档（按需）

事实来源：

- 产品背景与约束 → `docs/background/`
- 长期设计与领域术语 → `docs/design/`
- 阶段计划与任务切片 → `docs/plan/`
- 当前进展与风险 → `docs/status/`
- 固定数据契约 → `datasets/sales-analytics-v1/contract.json`

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档目录职责

- `docs/background/` — 外部输入、已确认的产品背景、需求与约束。不得改写以迁就实现。
- `docs/design/` — 长期边界、领域模型、接口、数据与关键决策。
- `docs/plan/` — 阶段目标、任务切片、依赖、验证与交付边界。
- `docs/status/` — 当前事实、进展、风险与下一步。
- `docs/index.md` — 文档导航与阅读入口，不复制正文。
- `docs/AGENTS.md` — `docs/` 子树规则与目录职责细则。

实现过程产生的产品内生文档应遵循上述项目文档结构；不要改写既有背景约束来迁就实现。子树规则在各自 `AGENTS.md` 中维护（如 `datasets/sales-analytics-v1/AGENTS.md`）。

## 长期模块与安全边界

- 首轮模块边界：Web（查询工作台）、API（查询执行与策略）、PostgreSQL（`platform` + `analytics` 双逻辑库）、`datasets/`（固定产品输入）。
- 安全边界：用户 SQL 只在 `analytics` 上以只读身份执行；`platform` 仅平台可写身份访问；数据库权限是应用层策略之外的第二道边界。详见 `docs/background/`。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## Git、验证与提交限制

- 不自动 commit、push、创建 worktree 或持续介入；提交时机由用户决定。
- 当前仓库真实验证命令：在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py` 校验固定数据集。
- 应用建立后，验证命令以 `docs/` 中的设计/计划文档和仓库依赖清单为准；不在 `AGENTS.md` 中维护会频繁变化的版本事实。
- 提交前运行 `git diff --check` 检查空白错误。

## 缓存、依赖与构建产物边界

- 不得提交：`.worktrees/`、`.env`（`.env.example` 除外）、`node_modules/`、`dist/`、`__pycache__/`、`.pytest_cache/`、`.venv/`、`playwright-report/`、`test-results/`。详见 `.gitignore`。
- 不得清理或自动纳入 `.worktrees/` 中的本地独立工作区。

## 工作区

- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
