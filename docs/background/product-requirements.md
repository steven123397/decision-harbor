# 产品需求

## 产品定位

DecisionHarbor 面向企业内部业务人员提供受治理的数据分析能力。长期方向可以扩展自然语言分析、语义层、权限与 AI 辅助；当前首轮只实现一条显式 SQL 查询链路，以建立跨前端、API、数据库和本地基础设施的可运行基础。

用户在最小查询工作台中提交 SQL。系统校验查询是否满足策略，通过独立只读数据库身份执行允许的查询，并返回结果或稳定的拒绝、失败信息，同时保留可追踪的查询记录。

## 目标

首轮交付必须满足以下目标：

1. 在只安装 Git、Docker 和 Docker Compose 的干净 WSL 环境中，可启动、迁移、填充数据并运行测试。
2. 提供受治理 SQL 查询执行模块，基于 SQL 抽象语法树（AST）和对象访问范围校验用户输入，并使用只读数据库身份执行。
3. 提供最小查询工作台，支持输入 SQL、提交、查看执行中状态，并展示结果表格或拒绝原因。
4. 记录原始 SQL、策略判定或拒绝原因、执行状态、返回行数、耗时、错误摘要和创建时间。

## 非目标

当前范围不包括：

- LLM、自然语言转 SQL、RAG、MCP 或 A2A；
- 用户注册、复杂 RBAC、完整运营后台或图表编辑器；
- 跨库访问平台状态，或使用平台写入身份执行用户 SQL。

首轮不以独立视觉改版为目标，但查询工作台仍需清晰呈现输入、运行状态、结果和错误，并满足基本的响应式可用性。后续功能演进可以同步改善视觉与交互，不因此扩大为完整运营前端。

## 固定分析数据

`datasets/sales-analytics-v1/contract.json` 是机器可读的数据契约，`datasets/sales-analytics-v1/data/` 包含权威 CSV。实现迁移和 seed 流程时，必须保留以下五张表及字段语义：

| 表 | 字段 |
| --- | --- |
| `customers` | `id`、`customer_code`、`display_name`、`region`、`segment`、`created_at` |
| `product_categories` | `id`、`category_code`、`name` |
| `products` | `id`、`sku`、`name`、`category_id`、`list_price`、`cost_price`、`active` |
| `orders` | `id`、`order_no`、`customer_id`、`ordered_at`、`status`、`currency` |
| `order_items` | `id`、`order_id`、`product_id`、`quantity`、`unit_price`、`discount_rate` |

公开数据固定包含 100 个客户、8 个产品类别、50 个产品、1,000 张订单和 3,000 条订单明细。订单日期覆盖 2024-01-01 至 2025-12-31，货币统一为 CNY。

订单状态为 `pending`、`confirmed`、`cancelled` 与 `refunded`。只有 `confirmed` 订单计入已实现销售额和毛利：

```text
sales_amount = quantity * unit_price * (1 - discount_rate)
cost_amount = quantity * products.cost_price
gross_margin = sales_amount - cost_amount
```

金额必须使用定点数，`discount_rate` 的范围为 0 到 1，订单总额不得作为冗余字段保存。

## 数据库与访问边界

本地环境使用一个 PostgreSQL 容器，承载两个逻辑数据库：

- `platform`：保存查询审计等产品状态，只允许平台可写身份访问。
- `analytics`：承载固定销售分析数据，只允许查询执行器使用独立只读身份访问。

用户 SQL 必须在 `analytics` 上使用只读身份执行。数据库权限是应用层策略之外的第二道边界。

## SQL 治理规则

允许一条 PostgreSQL 只读查询表达式，包括 `SELECT`、`WITH ... SELECT`、连接、子查询、聚合、窗口函数，以及 `UNION`、`INTERSECT`、`EXCEPT`。

必须拒绝以下输入或访问：

- 多条语句；
- `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO`；
- 数据修改型 CTE 与 `SELECT INTO`；
- 非 `analytics` 业务表、系统目录或未授权对象；
- 任何绕过只读数据库身份的执行方式。

策略检查必须基于 SQL AST 和对象访问范围，不能只依赖字符串黑名单。执行器必须配置语句超时和结果行数上限，并为每次策略判定、拒绝或执行结果保留稳定记录。

## 最小外部 API

以下端点构成产品首轮的最小 HTTP 接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 检查进程存活。 |
| `GET` | `/ready` | 仅在数据库和迁移就绪时返回成功。 |
| `POST` | `/api/v1/query-runs` | 提交 `{ "sql": "..." }`，返回查询记录、策略拒绝或执行错误。 |
| `GET` | `/api/v1/query-runs/{id}` | 读取指定查询记录的状态和审计事实。 |

响应使用统一 JSON 结构，至少区分 `succeeded`、`rejected` 与 `failed`。成功响应包含列定义、行数据、行数与耗时；拒绝或失败响应包含稳定错误码、可读说明与记录标识。

## 本地运行与验证要求

项目应提供一条统一命令，在干净 WSL 环境中完成构建、启动 Web、API 与 PostgreSQL，创建两个逻辑数据库和所需身份，执行迁移与数据填充，并等待健康检查与就绪状态。重复启动、迁移和 seed 不得产生破坏性重复。

项目还应提供一条统一测试命令，至少覆盖 SQL 策略单元测试、双数据库集成测试和最小查询工作台的浏览器主流程。

多个本地工作区必须可以并行运行：Compose 项目名、Web/API 宿主端口应可配置；不得使用固定 `container_name`、全局网络名、全局数据卷名或共享绑定目录。数据库不要求暴露固定宿主端口。
