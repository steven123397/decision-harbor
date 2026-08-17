# DecisionHarbor Agent 工作规则

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 开发前阅读

开始实现前，先阅读：

- `README.md`
- `CONTEXT.md`（领域词汇表；产出命名与文档一律使用其中的规范术语，不漂移到被回避的同义词）
- `docs/background/product-requirements.md`
- `docs/background/technical-constraints.md`
- `datasets/sales-analytics-v1/README.md`
- `datasets/sales-analytics-v1/contract.json`
- 与当前改动相关的 `docs/adr/` 决策记录

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档结构

- `CONTEXT.md`：领域词汇表，只放术语定义，不放实现细节；术语敲定时随手更新。
- `docs/adr/`：不可逆决策记录，顺序编号，一个决策一个文件。产出与既有 ADR 矛盾时必须显式标出（「与 ADR-NNNN 矛盾，但值得重开，因为……」），不得静默覆盖。
- `docs/agents/`：工程技能（mattpocock/skills）的仓库级配置，见下。
- `docs/background/`：已确认的外部输入（产品需求与技术约束）。只有用户确认变更意图后才修改；不得改写背景约束来迁就实现。
- ZCode 仓库 wiki：客户端从某次 commit 快照派生的代码导览，存放于仓库外（`~/.zcode/v2/repo-wiki/`）。它是只读参考，不是事实源，与仓库文档冲突时以仓库文档为准；随代码演进过期，需要时重新生成，不手工维护；仓库文档与 Issues 不引用 wiki 页面。
- 规格与工单不再落在 `docs/`，统一进入 GitHub Issues（见下）。

## Agent skills

### Issue tracker

规格、工单与决策票统一记录在 GitHub Issues（`steven123397/decision-harbor`），经 `gh` CLI 读写。见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个默认分流标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根 `CONTEXT.md` + `docs/adr/`。见 `docs/agents/domain.md`。

## 工作区

- `v0.1.0/zcode-glm-5.3-high` 是独立发展的工程线，不合并回 `main`，也不以 `main` 为变更或 PR 目标；`main` 仅作参考对照。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的文档（`CONTEXT.md`、相关 ADR、`README.md`）与测试。
