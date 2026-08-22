---
status: implemented
date: 2026-07-20
---

# 用显式 JSON 类型保留查询结果值

## Context

PostgreSQL 的 `bigint` 和 `numeric` 可能超出 JavaScript 安全整数或二进制浮点精度，日期时间也不能靠驱动默认值形成稳定 HTTP 合同。对未知类型统一调用 `str()` 会掩盖语义差异，并让客户端无法判断值的解释方式。

## Decision

结果列携带稳定的 PostgreSQL 类型名称；`bigint` 和 `numeric` 使用十进制字符串，日期与时间戳使用 ISO 8601 字符串，布尔值、32 位整数、文本和 `null` 使用对应 JSON 原生类型。未明确支持的类型使运行以 `unsupported_result_type` 失败，不进行隐式字符串化。

## Considered options

- 所有值都使用 JSON number 或驱动默认编码：拒绝。浏览器可能静默损失大整数和定点数精度。
- 所有值都转成字符串：拒绝。它会丢失布尔值、普通整数和空值的稳定类型语义。
- 对未知类型使用 `str()`：拒绝。输出格式受驱动和运行环境影响，无法作为公开合同。

## Consequences

客户端必须结合列类型解释字符串数值和时间值；新增 PostgreSQL 结果类型需要显式设计序列化和测试。异步结果快照必须保存同一表示，不能在持久化边界重新推断类型。

## Implementation and evidence

提交 `6e3964f` 在 `executor.py` 中建立显式 OID 允许集和单元格序列化；`test_executor.py` 覆盖 `numeric`、`bigint`、日期时间、原生 JSON 类型与未知类型拒绝。

## Revisit when

外部 API 改用能够无损表达十进制数和强类型时间的协议，或产品引入正式的类型扩展机制时，重新评估 JSON 表示。
