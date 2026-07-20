# 文档索引

DecisionHarbor 正式文档的导航入口。新 Agent 建议按以下顺序阅读：背景 → 设计 → 计划 → 状态。各目录职责与写作规则见 [docs 子树规则](AGENTS.md)。

## background — 外部输入与约束

已确认的产品背景、需求和技术约束，是设计、计划与实现的输入。

- [产品需求](background/product-requirements.md)
- [技术约束](background/technical-constraints.md)

## design — 长期设计与决策

长期边界、领域模型、接口、数据与关键决策。写作规范见 [design 目录说明](design/README.md)。

- [首轮总体设计](design/overview.md)：范围、模块与数据流、不变量、关键决策、延后决策。
- [查询治理设计](design/query-governance.md)：查询运行状态、审计事实、AST 策略与资源限制。
- [API 与查询工作台设计](design/api-and-workbench.md)：最小 HTTP 接口、错误语义与工作台行为。
- [数据与运行环境设计](design/data-and-runtime.md)：双数据库、身份边界、迁移与幂等 seed、Compose 并行隔离。
- [测试设计](design/testing.md)：单元、集成与浏览器测试接缝。

## plan — 阶段计划

阶段目标、任务切片、依赖、验证与交付边界。写作规范见 [plan 目录说明](plan/README.md)。

- [首轮实现计划](plan/first-round-implementation.md)：首轮系统的任务切片、依赖与验证方式。

## status — 当前状态

当前事实、进展、风险与下一步。维护规则见 [status 目录说明](status/README.md)。

- [项目状态](status/project_status.md)

## 其他资料

- [销售分析数据集 v1](../datasets/sales-analytics-v1/README.md)：固定分析数据的契约、生成与校验说明，属于产品输入。
