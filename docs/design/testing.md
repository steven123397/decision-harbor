# 验证接缝

## 范围

首轮三层验证的设计：单元、集成与浏览器。验证层次与质量基线来自 [../background/technical-constraints.md](../background/technical-constraints.md)；本文定义各层的接缝与运行形态。覆盖率不是目标。

## 统一命令

- `make up`：构建并启动完整栈（一次性 init 完成迁移与 seed），等待健康与就绪（见 [architecture.md](architecture.md)）。
- `make test`：按序执行策略与 API 单元测试 → 双数据库集成测试（一次性 `api-test` 容器）→ Web 单元测试（一次性 `web-test` 容器）→ 浏览器测试；前置保证 compose 栈已 `up --wait`。

## 策略单元测试

接缝是策略纯函数 `evaluate(sql) -> PolicyDecision`（见 [query-governance.md](query-governance.md)），无数据库依赖。用例按判定步骤分组，表驱动：

- **允许**：`SELECT`、`WITH ... SELECT`、连接、子查询、聚合、窗口（含窗口帧）、`UNION`、`INTERSECT`、`EXCEPT`、递归 CTE、`CASE`、`DISTINCT`、安全类型转换、限定 `analytics` schema 的引用。
- **拒绝**：多条语句；背景列出的全部写语句与命令；数据修改型 CTE；`SELECT INTO`；非 `analytics` 对象与系统目录；未授权裸表名；函数白名单之外的函数；不安全的 CAST 目标类型与越界类型参数；支持节点白名单之外的结构；占位符。
- **绕过向量回归**：CTE 名遮蔽系统目录（含非递归自引用）、作用域外借用 CTE 名、`::regclass` 对象标识转换、`query_to_xml`、`current_user` 等逐一锁定拒绝码。
- **边界**：尾部分号、注释混排、关键字大小写、空输入与超长输入。

资源限制的配置解析（超时、行数、长度与并发上限从环境变量读取、带默认值并校验上下界）另设单元用例；契约 → DDL 派生（含 CHECK 约束）同样有纯函数单元用例。

## 双数据库集成测试

接缝是 compose 栈中真实的 PostgreSQL 与已完成的引导，pytest 运行在一次性 `api-test` 容器内（owner 连接串只注入该容器，不进入 API 服务进程）：

- **身份职责分离**：`platform_app` 可读写 `query_runs`；`analytics_readonly` 对五张表 SELECT 成功，任何写入、DDL 与未授权对象访问被数据库拒绝。
- **契约约束**：违反 `allowed_values`、折扣区间与成本/标价规则的 INSERT 被 CHECK 约束拒绝。
- **绕过向量端到端回归**：已知治理绕过 SQL 经 HTTP API 提交全部返回 `rejected`。
- **seed 幂等**：栈二次启动后五表行数等于 `expected_counts`，标记不变、无重复数据。
- **端到端链路**：经 HTTP API 提交允许查询返回结果与审计事实；提交违规 SQL 返回 `rejected` 且审计落库；执行错误返回 `failed` 与稳定错误码；`GET /api/v1/query-runs/{id}` 可读取终态，非整数 id 返回 404。

## Web 单元与浏览器测试

Web 单元测试（Vitest）运行在专门的 `web-test` 测试镜像（compose `test` profile），不进入生产 Nginx 容器；保持最小：响应解析到视图状态的映射是唯一值得锁定的纯逻辑。

Playwright 驱动真实工作台（`http://localhost:${WEB_PORT}`）：

- **主流程**：输入允许 SQL → 执行中状态可见 → 结果表格与元信息正确。
- **拒绝流程**：输入违规 SQL → 拒绝码与说明展示。
- **失败流程**：执行错误（列不存在）与语句超时 → 失败面板与稳定失败码展示；长查询期间「执行中」状态可见。

定位依赖 `data-testid`（见 [workbench.md](workbench.md)），不依赖文案快照之外的视觉细节。

## 边界

- 集成与浏览器测试共享同一个 compose 栈，不各自起独立数据库；并行工作区的隔离由 [architecture.md](architecture.md) 的隔离规则保证。
- 测试容器（`api-test`、`web-test`）只在 `test` profile 构建运行，生产镜像与凭据面不受影响。
