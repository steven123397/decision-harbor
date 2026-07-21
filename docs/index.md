# DecisionHarbor 文档索引

本索引是文档导航与阅读入口，不复制正文。完整协作规则见根 [`AGENTS.md`](../AGENTS.md) 与 [`docs/AGENTS.md`](AGENTS.md)。

## 默认阅读顺序

1. [`README.md`](../README.md) — 项目概述与文档入口
2. [`background/product-requirements.md`](background/product-requirements.md) — 产品需求
3. [`background/technical-constraints.md`](background/technical-constraints.md) — 技术约束
4. [`../datasets/sales-analytics-v1/README.md`](../datasets/sales-analytics-v1/README.md) 与 [`contract.json`](../datasets/sales-analytics-v1/contract.json) — 固定销售分析数据
5. [`status/project_status.md`](status/project_status.md) — 当前事实、进展与下一步
6. 按需阅读 [`design/`](design/) 与 [`plan/`](plan/)

## 设计

- [`design/first-round.md`](design/first-round.md) — 首轮权威设计：显式 SQL 受控执行链路
- [`design/CONTEXT.md`](design/CONTEXT.md) — 领域术语与概念边界

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `background/` | 外部输入、已确认的产品背景、需求与约束 |
| `design/` | 长期边界、领域模型、接口、数据与关键决策 |
| `plan/` | 阶段目标、任务切片、依赖、验证与交付边界 |
| `status/` | 当前事实、进展、风险与下一步 |

## 子树规则

- [`docs/AGENTS.md`](AGENTS.md) — docs 子树规则与目录职责细则
- [`../datasets/sales-analytics-v1/AGENTS.md`](../datasets/sales-analytics-v1/AGENTS.md) — 固定数据集子树规则
