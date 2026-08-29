---
status: implemented
date: 2026-07-20
---

# 叠加 AST 策略与数据库只读权限

## Context

数据库只读权限可以阻止写入，却不能表达产品允许的表、函数、类型转换和 SQL 形态。应用策略可以限制产品范围，但解析或允许集缺陷不应成为唯一安全边界。

## Decision

用户 SQL 必须先通过基于 PostgreSQL AST、对象范围和允许集的默认拒绝策略，再由 analytics 只读身份执行。两层边界相互独立，任何一层都不能替代另一层。

## Considered options

- 使用字符串黑名单过滤危险关键字：拒绝。它无法可靠处理嵌套结构、别名、CTE、类型转换和语法变体。
- 只依赖数据库只读身份：拒绝。只读身份仍可能读取系统对象、调用危险函数或访问产品范围外的数据。
- 只依赖 AST 策略：拒绝。解析器差异或策略遗漏可能让写入能力到达数据库。

## Consequences

新增 SQL 形态、函数或类型转换必须同时扩展策略允许集和真实 PostgreSQL 证据。策略保持默认拒绝会限制部分合法查询，但安全边界不会因应用层单点遗漏直接退化为数据库写入。

## Implementation and evidence

提交 `6e3964f` 实现 SQLGlot AST 策略、对象允许集和 analytics 只读执行；提交 `37764b0` 收紧对象标识类型转换。`test_policy.py` 覆盖允许形态、DML/DDL、多语句、系统对象和函数边界，`test_query_chain.py` 使用真实 API 覆盖策略拒绝、未授权对象及标识转换探针，`tests/worker/test_query_execution.py` 在 Worker 凭据环境中证明允许查询仍由 analytics 只读身份有界执行。v0.2.0 起策略判定发生在提交阶段，执行只发生在 Worker 进程。

## Revisit when

更换 SQL 方言、解析器或分析存储，或需要开放新的 schema、函数和类型族时，重新评估允许集；双层治理原则保持不变，除非替代执行环境能提供等价的独立约束。
