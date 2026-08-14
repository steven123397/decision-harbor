# 领域术语（CONTEXT）

本文件只维护 DecisionHarbor 项目特有的领域概念、边界与推荐用词；不写实现细节、阶段任务或当前状态。设计决策见 [system-design.md](system-design.md)，事实来源为 `docs/background/` 与 `datasets/sales-analytics-v1/contract.json`。

## 查询运行（query run）

一次用户 SQL 提交的完整生命周期记录，是审计与状态展示的基本单元，对应 API 资源 `/api/v1/query-runs/{id}`。规范名：**查询运行** / **query run**；避免与“请求”“查询结果”混用。

## 策略判定（policy verdict）

治理模块对一条 SQL 给出的结论，取值 `allowed`（允许）或 `denied`（拒绝）。判定发生在执行之前，仅基于 AST 与对象访问范围。

## 运行状态（run status）

查询运行所处的状态，规范取值：

- `running`：策略已允许，正在执行（唯一的非终态）。
- `succeeded`：执行成功并返回结果（终态）。
- `failed`：策略通过但执行出错（超时、超出行数上限、数据库错误等，终态）。
- `rejected`：策略拒绝，未进入执行（终态）。

边界：`rejected` 只表示“策略拒绝”，`failed` 只表示“策略通过但执行出错”，二者不混用。

## 审计事实（audit facts）

查询运行的持久化元数据：原始 SQL、策略判定或拒绝原因、运行状态、返回行数、耗时、错误摘要与创建时间。只存事实，不存结果行。

## 结果集（result）

执行成功后返回的列定义与行数据，仅通过有界内存缓存短期提供，不属于持久化审计事实。

## 对象访问范围（object access scope）

策略允许查询访问的授权对象集合，即 `contract.json` 声明的五张 `analytics` 表（customers、product_categories、products、orders、order_items）。任何超出该范围的表引用（含系统目录、其他 schema 或数据库）都拒绝。

## 身份（role）

- `analytics_reader`（只读身份）：执行器唯一使用的分析库身份，仅有 `analytics` 五张表的 `SELECT`。
- `platform_writer`（平台可写身份）：API 用于读写 `platform` 审计状态的身份。
- `dh_admin`（引导身份）：仅在迁移与 seed 阶段创建 schema、加载数据，运行时不被用户查询使用。

## 错误码（error code）

稳定字符串，用于拒绝与失败响应的 `error.code`。前缀区分来源：`POLICY_*`（策略拒绝）、`EXEC_*`（执行失败）、`INVALID_REQUEST` / `NOT_FOUND`（请求错误）。完整清单见 system-design.md 第 7 节。

## 幂等 seed（idempotent seed）

重复执行不产生破坏性重复的数据填充流程；`analytics` 因仅承载权威固定数据，采用“截断后从权威 CSV 重载”达成幂等。

## 已实现销售额（realized sales）

仅 `confirmed` 订单计入的销售额与毛利口径，公式以 `contract.json` 与 `docs/background/product-requirements.md` 为准，本文件不另行定义。
