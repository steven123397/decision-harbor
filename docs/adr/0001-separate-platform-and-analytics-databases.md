---
status: implemented
date: 2026-07-20
---

# 分离 platform 与 analytics 数据库身份

## Context

DecisionHarbor 既要保存可写的产品状态，又要执行不可信的用户 SQL。本地环境需要保持单命令启动和多工作树隔离，但仅靠应用代码约定连接用途，无法构成可独立验证的权限边界。

## Decision

在同一 PostgreSQL 实例中使用独立的 `platform` 与 `analytics` 逻辑数据库，并为平台写入、分析查询和就绪探测分配不同的最小权限身份。用户 SQL 只能使用 analytics 查询身份执行；API 和 Worker 不能获得引导身份。

## Considered options

- 在单一数据库中只用 schema 和代码约定隔离：拒绝。平台写入身份一旦误入查询执行路径，数据库无法阻止用户 SQL 访问产品状态。
- 为两个数据库分别运行 PostgreSQL 容器：首轮不采用。它能提供更强的运行隔离，但会增加本地启动、迁移和多工作树资源成本。

## Consequences

引导流程必须创建两个数据库、维护两套迁移和显式授权；就绪探测需要独立最小权限身份。平台与分析数据不能依赖跨数据库事务，但权限边界可以通过真实 PostgreSQL 测试独立证明。

## Implementation and evidence

提交 `6e3964f` 建立双数据库、运行时身份、迁移、固定数据 seed 和 Compose 初始化。`test_postgres_contract.py` 证明数据库连接范围、默认只读事务、业务表读取、写入/DDL 拒绝和维护 schema 隔离；统一测试入口在当前基座上通过。

## Revisit when

进入生产部署并需要物理数据库隔离、独立扩缩容或不同备份恢复策略时，重新评估单实例双逻辑数据库；运行时身份职责仍必须保持分离。
