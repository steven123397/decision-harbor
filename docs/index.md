# 文档入口

本页只提供正式文档的导航、默认阅读顺序和目录职责，不复制背景资料或实现正文。

## 默认阅读顺序

1. [项目 README](../README.md)
2. [产品需求](background/product-requirements.md)
3. [技术约束](background/technical-constraints.md)
4. [销售分析数据集 v1 说明](../datasets/sales-analytics-v1/README.md)
5. [销售分析数据契约](../datasets/sales-analytics-v1/contract.json)
6. [项目状态](status/project_status.md)
7. 与任务相关的设计文档和阶段计划。

## 目录职责

| 目录 | 职责 | 当前入口 |
| --- | --- | --- |
| `background/` | 外部输入、产品需求和已确认技术约束。 | [产品需求](background/product-requirements.md)、[技术约束](background/technical-constraints.md) |
| `design/` | 长期有效的产品内生设计、边界和关键决策。 | [设计文档规则](design/README.md)、[首轮受治理查询链路设计](design/first-round-governed-query-design.md) |
| `plan/` | 阶段目标、任务切片、依赖、验证和交付边界。 | [计划文档规则](plan/README.md)、[首轮实现计划](plan/first-round-implementation.md) |
| `status/` | 当前结论、进展、风险和下一步。 | [状态文档规则](status/README.md)、[项目状态](status/project_status.md) |

## 数据输入

固定销售分析数据是产品输入的一部分，数据集目录中的契约、CSV、生成器、校验器和 manifest 必须作为一个整体阅读和维护：

- [数据集说明](../datasets/sales-analytics-v1/README.md)
- [数据契约](../datasets/sales-analytics-v1/contract.json)
