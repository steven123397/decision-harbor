# 领域术语与概念边界

本文件维护 DecisionHarbor 的 ubiquitous language，只收录已在 [`background/`](../background/) 或 [`数据契约`](../../datasets/sales-analytics-v1/contract.json) 中确认的术语。新增术语需先在背景或设计中确认。

## 查询链路

- **查询工作台（Query Workbench）**：用户提交显式 SQL 的最小前端界面。
- **查询记录（Query Run）**：一次 SQL 提交的完整审计单元，记录原始 SQL、策略判定或拒绝原因、执行状态、返回行数、耗时、错误摘要与创建时间。
- **策略判定（Policy Decision）**：基于 SQL AST 与对象访问范围对用户输入作出的允许或拒绝决定；不依赖字符串黑名单。
- **只读执行身份**：在 `analytics` 数据库上执行用户 SQL 的独立受限身份。
- **平台写入身份**：在 `platform` 数据库上写入查询审计等产品状态的身份。

## 数据库边界

- **platform**：保存查询审计等产品状态，仅平台可写身份访问。
- **analytics**：承载固定销售分析数据，仅查询执行器以只读身份访问。
- 数据库权限是应用层策略之外的第二道边界。

## 销售分析口径

源自 `datasets/sales-analytics-v1/contract.json` 的 `business_rules`：

- **sales_amount** = `quantity * unit_price * (1 - discount_rate)`
- **cost_amount** = `quantity * products.cost_price`
- **gross_margin** = `sales_amount - cost_amount`
- **已实现销售**：仅 `confirmed` 状态订单计入销售额与毛利。
- 金额使用定点数；`discount_rate` 范围 0–1；订单总额不得作为冗余字段保存。

## 查询状态

查询记录的终态至少区分：`succeeded`、`rejected`、`failed`。
