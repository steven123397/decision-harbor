# sales-analytics-v1 子树规则

本目录是 DecisionHarbor 的固定产品输入，不是应用代码。本文件在其作用域内补充根 [`AGENTS.md`](../../AGENTS.md)。

## 事实来源

- `contract.json` 是机器可读的数据契约与业务口径，定义五张表、字段、关联、计数与业务规则。
- `data/*.csv` 是当前版本的权威公开数据。
- `manifest.json` 记录版本、seed、行数与哈希，使变更可检测、可复现。
- `generate.py` 用固定默认 seed 复现数据；`validate.py` 校验已提交文件、关联、业务边界与确定性复现。

## 不可变边界

- 实现迁移与 seed 流程时，不得改名、删除或重新解释 `contract.json` 中的字段与业务口径。
- `sales_amount`、`cost_amount`、`gross_margin` 的计算口径以 `contract.json` 的 `business_rules` 为准。
- 金额使用定点数；`discount_rate` 范围 0–1；订单总额不得作为冗余字段保存。
- 只有 `confirmed` 订单计入已实现销售额与毛利。

## 本地验证

在本目录运行：

```bash
python3 validate.py
```

生成器仅依赖 Python 标准库。已提交 CSV 是权威数据；生成器与 manifest 使变更可检测。
