# 领域术语

只维护术语与概念边界。业务口径的权威定义在 [产品需求](../background/product-requirements.md) 与 [`contract.json`](../../datasets/sales-analytics-v1/contract.json)，本文不重述公式与字段。

## 查询链路

- **受治理查询**：用户提交的显式 SQL，必须先通过策略检查、再以只读身份执行。
- **查询记录（query run）**：一次 SQL 提交产生的审计事实，含原始 SQL、策略判定、执行状态、行数、耗时与错误摘要。
- **策略判定**：基于 SQL AST 与对象访问范围得出的允许或拒绝结论；拒绝必须给出稳定错误码。
- **执行状态**：对外统一区分 `succeeded`、`rejected`、`failed` 三类终态；`running` 是记录已创建、尚未到达终态的过渡态。
- **截断（truncated）**：结果行数达到上限时只返回前 N 行，并在响应与审计中显式标记，区别于完整结果。

## 数据与身份

- **`platform` 库**：保存查询审计等产品状态，仅平台可写身份访问。
- **`analytics` 库**：承载固定销售分析数据，用户 SQL 仅经独立只读身份在此执行。
- **固定数据集**：`datasets/sales-analytics-v1/` 中已提交的 CSV 是权威数据，可重复生成并校验。

## 业务口径

- **已实现销售额 / 毛利**：仅 `confirmed` 订单计入；计算公式以 `contract.json` 的 `business_rules` 为准。
