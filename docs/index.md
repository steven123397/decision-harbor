# DecisionHarbor 文档索引

本页只负责导航，不复制正文。默认阅读顺序见根 [`AGENTS.md`](../AGENTS.md)；各目录职责与写作规则见 [`docs/AGENTS.md`](AGENTS.md)。

## background/ — 已确认的外部输入

产品背景、需求与约束的来源，实现不得改写它们来迁就自身。

- [产品需求](background/product-requirements.md)
- [技术约束](background/technical-constraints.md)

固定数据集及其契约位于 [`datasets/sales-analytics-v1/`](../datasets/sales-analytics-v1/README.md)，与本目录同属已确认输入。

## design/ — 长期设计事实

模块边界、领域模型、接口、数据与关键决策。

- [写作说明](design/README.md)
- [领域术语](design/CONTEXT.md)
- [系统架构](design/architecture.md) — 首轮范围、模块与数据流、双数据库身份边界
- [查询治理策略与资源限制](design/query-governance.md) — AST 判定规则、对象范围、超时与行上限
- [查询记录与最小 API](design/query-runs-api.md) — 状态机、审计表、端点语义与错误码注册表
- [查询工作台](design/workbench.md) — 最小前端界面、状态展示与测试锚点
- [本地运行环境](design/local-runtime.md) — Compose 隔离、迁移与幂等 seed、统一命令

## plan/ — 阶段计划

阶段目标、任务切片、依赖与验证方式。

- [写作说明](plan/README.md)
- [v0.1 首轮基座与受治理查询链路](plan/v0.1-foundation.md)

## status/ — 当前事实

当前结论、进展、风险和下一步。

- [写作说明](status/README.md)
- [项目状态](status/project_status.md)
