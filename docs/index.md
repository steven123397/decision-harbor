# 文档索引

本页只提供阅读入口，不复制正文。恢复项目状态时，先读根 `AGENTS.md` 和 `README.md`，再按下面顺序进入正式文档。

## 阅读顺序

1. [当前状态](status/project_status.md) — 恢复进展、风险和下一步
2. [受治理查询链路](design/governed-query-path.md) — 首轮可实现设计
3. [规范术语](design/CONTEXT.md) — 领域用词与概念边界
4. [产品需求](background/product-requirements.md) — 首轮目标、非目标、SQL 治理与最小 API
5. [技术约束](background/technical-constraints.md) — 技术栈、架构边界、隔离与质量基线
6. [销售分析数据集 v1](../datasets/sales-analytics-v1/README.md) 与 [数据契约](../datasets/sales-analytics-v1/contract.json) — 表、字段和业务口径
7. [首轮交付计划](plan/v0.1-first-delivery.md) — 阶段切片与验证门禁

## 目录职责

| 目录 | 职责 | 入口 |
| --- | --- | --- |
| `background/` | 外部输入、需求背景和约束来源 | [产品需求](background/product-requirements.md)、[技术约束](background/technical-constraints.md) |
| `design/` | 长期边界、领域模型、接口、数据与关键决策 | [受治理查询链路](design/governed-query-path.md)、[规范术语](design/CONTEXT.md) |
| `plan/` | 阶段目标、任务切片、依赖、验证和交付边界 | [首轮交付计划](plan/v0.1-first-delivery.md) |
| `status/` | 当前事实、进展、风险和下一步 | [项目状态](status/project_status.md) |

文档角色细则见 [docs/AGENTS.md](AGENTS.md)。状态维护方式见 [status/README.md](status/README.md)。
