# 首轮实现计划

依赖顺序：API 核心（策略→执行→存储→编排）→ 数据库引导/seed → Web → Compose → 集成/浏览器测试 → 全量验证。按序推进，不停在计划阶段。

## 阶段与任务

1. **API 骨架与 SQL 策略（无 DB）**：config、schema_catalog（contract 驱动授权对象/DDL）、policy（AST+对象范围）。策略单元测试先行（先写失败测试再实现）。
2. **数据库引导与 seed**：建库建角色、Alembic platform（query_runs、dataset_seed）、analytics 建表+截断重载、幂等标记、授权。
3. **执行器、审计存储、编排服务**：只读执行+语句超时+行上限、query_run 状态机、内存结果缓存、启动收敛遗留 running。
4. **HTTP 接口**：/health、/ready、query-runs POST/GET、统一封套与稳定错误码。
5. **Web 工作台**：输入/提交/轮询/结果表/拒绝展示。
6. **Compose 与统一命令**：并行隔离、Makefile up/test、wait-ready。
7. **集成测试（双库身份分离）+ 浏览器主流程测试**。
8. **全量验证**：git diff --check、数据集校验、项目测试、Compose 启动、/health、/ready、允许/拒绝查询 API 证据、浏览器主链。

## 依赖

1 → 2 → 3 → 4 → 5；6 贯穿；7 依赖 3–6；8 收尾。

## 验证（每阶段）

- 策略单测：允许（SELECT/WITH/连接/子查询/聚合/窗口/集合运算）、拒绝（多语句/写语句/数据修改 CTE/SELECT INTO/未授权对象/系统目录）、边界（解析错误/空输入/字符串关键字不误判）。
- 集成：seed 幂等、platform_writer 可写审计、analytics_reader 可读但 INSERT 被拒、超时与行上限归为 EXEC_*、状态机落库、启动收敛。
- 浏览器：输入→提交→执行中→结果表；被拒 SQL→拒绝原因。
- 统一：`make test` 编排以上三者。

## 未规划区

不实现 auth、结果持久化/分页/导出、查询取消、多实例队列（见设计第 9 节延后决策）。

## 交付边界

首轮目标全部满足即完成；不扩展到延后决策。
