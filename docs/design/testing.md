# 测试设计

## 范围

首轮三层验证的接缝与覆盖点，对应 [技术约束](../background/technical-constraints.md) 的质量基线。测试数量与覆盖率不是目标；每层证明一件事。统一测试命令的形态在计划阶段确定，本文约束其必须触达的内容。

## 单元测试（pytest，无数据库）

目标：证明 AST 策略判定的正确性。策略模块为纯函数（见 [查询治理设计](query-governance.md)），测试不依赖数据库与网络。

- 允许矩阵：`SELECT`、`WITH ... SELECT`、连接、子查询、聚合、窗口函数、`UNION` / `INTERSECT` / `EXCEPT`。
- 拒绝矩阵：多语句；`INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO`；数据修改型 CTE；`SELECT INTO`；`pg_catalog` 与 `information_schema`；白名单外表；跨 schema 引用。
- 边界语法：CTE 名与白名单表同名（遮蔽）、引号包裹标识符、`analytics.` 显式限定、表别名、子查询与 CTE 内的表引用、大小写变体。

## 前端单元测试（Vitest）

目标：证明工作台渲染分支与响应映射正确。覆盖：执行中状态、结果表格、截断提示、拒绝面板、失败面板、请求校验失败的提示。

## 集成测试（pytest，Compose 网络内真实双库）

目标：证明双身份职责分离与受治理链路端到端行为。测试在 Compose 网络内运行，不依赖数据库宿主端口。

- 身份边界：`analytics_readonly` 不能写五表、不能 DDL、不能访问 `platform`；`platform_app` 不能读 `analytics`。
- API 端到端：允许查询 → `succeeded`（列定义、行、行数、耗时、截断语义）；拒绝输入 → `rejected` 且 `GET /api/v1/query-runs/{id}` 可读回同一记录；调低 `statement_timeout` 触发超时 → `failed` + `QUERY_TIMEOUT`；未知记录 id → `404`。
- 就绪语义：迁移完成前后 `/ready` 的 `503` / `200` 切换，`/health` 始终 `200`。
- seed 幂等：重复执行 seed 后行数与内容一致（对照契约 `expected_counts` 抽查）。

## 浏览器测试（Playwright，真实 Compose 栈）

目标：证明最小查询工作台主流程在真实环境中可用。容器化运行，经 Web 宿主端口访问：打开工作台 → 输入允许的 SQL → 提交 → 观察到执行中状态 → 结果表格出现；输入被拒绝的 SQL → 拒绝原因与错误码可见。

## 延后

- 策略模块的模糊/随机语料测试。
- 性能与并发压测。
