# HTTP API 与错误语义

## 范围

首轮四个端点、统一响应结构与错误语义。端点清单本身是背景已固定的产品输入（见 [../background/product-requirements.md](../background/product-requirements.md)），本文定义其行为细节。运行状态机与策略拒绝码见 [query-governance.md](query-governance.md)。

## 端点

| 方法与路径 | 行为 |
| --- | --- |
| `GET /health` | 进程存活检查，不访问数据库，恒 200。 |
| `GET /ready` | 依次验证：`platform_app` 可连接且 `query_runs` 可查询、`analytics_readonly` 可连接且对五张表有轻量 SELECT 权限、seed 标记匹配。全部通过返回 200，否则 503 与原因说明。 |
| `POST /api/v1/query-runs` | 接收 `{ "sql": "..." }`，同步执行完整链路，返回终态。 |
| `GET /api/v1/query-runs/{id}` | 返回指定运行的审计事实。 |

## 运行状态

`running → succeeded | rejected | failed`，后三者是终态：

- `succeeded`：策略通过且执行成功，含结果与统计。
- `rejected`：策略判定不通过，含稳定拒绝码与可读说明。
- `failed`：策略通过但执行出错（含超时与资源繁忙），含稳定错误码与摘要。容量耗尽（`QY_CAPACITY_EXCEEDED`）与数据库不可达（`QY_ANALYTICS_UNAVAILABLE`）属于执行侧失败而非策略拒绝，统一落在 `failed`，客户端不会把资源问题误读成 SQL 违规。

POST 同步等待终态后返回；`running` 是持久化的中间态，用于崩溃取证与并发下的 GET 可见性。前端「执行中」由请求未返回呈现，无需轮询。理由：首轮无作业队列，执行时长由语句超时界定，同步语义最简单且完整保留审计链。

## 统一响应

产品端点统一返回 `outcome` 字段区分三种结果：

```json
{
  "outcome": "succeeded",
  "run": {
    "id": 1, "state": "succeeded", "sql": "SELECT ...",
    "row_count": 10, "truncated": false, "duration_ms": 42,
    "rejection_code": null, "rejection_message": null,
    "error_code": null, "error_message": null,
    "created_at": "...", "finished_at": "..."
  },
  "result": {
    "columns": [{ "name": "region", "type": "varchar" }],
    "rows": [["East"], ["West"]]
  }
}
```

`rejected` 与 `failed` 返回同一 `run` 结构（对应字段非空），且没有 `result`。GET 返回同一 `run` 结构。

行数据中的数值与时间戳一律以字符串传输：`numeric` 定点数经 JSON number 会退化为浮点，破坏背景的定点数约束；前端按文本渲染（见 [workbench.md](workbench.md)）。

## HTTP 状态码与错误码

**决策：策略拒绝与执行失败返回 HTTP 200**，HTTP 4xx/5xx 只用于协议与传输层问题。理由：拒绝与失败是产品定义内的结果，都有审计记录和记录 id；用 200 + `outcome` 让客户端统一按 envelope 分支，避免业务结果与传输错误混叠，也让失败响应可携带记录标识。此约定影响外部行为，实现与测试须一致遵守。

| 状态 | 场景 | 错误码 |
| --- | --- | --- |
| 400 | 请求体不是含字符串 `sql` 的对象（含多余字段与非法 JSON） | `QY_INVALID_REQUEST` |
| 404 | 指定 id 不存在（含非整数 id，如 `/query-runs/does-not-exist`） | `QY_RUN_NOT_FOUND` |
| 503 | `/ready` 未就绪 | 原因说明 |
| 200 + `rejected` | 策略拒绝 | `QY_SQL_TOO_LONG`、`QY_INVALID_SYNTAX`、`QY_MULTIPLE_STATEMENTS`、`QY_FORBIDDEN_STATEMENT`（含行锁子句）、`QY_SELECT_INTO`、`QY_WRITE_CTE`、`QY_UNAUTHORIZED_OBJECT`、`QY_FORBIDDEN_FUNCTION`、`QY_UNSUPPORTED_SQL` |
| 200 + `failed` | 执行错误 | `QY_TIMEOUT`、`QY_EXECUTION_ERROR`、`QY_CAPACITY_EXCEEDED`、`QY_ANALYTICS_UNAVAILABLE` |

非整数路径 id 属于「记录不存在」（404 + `QY_RUN_NOT_FOUND`），不会被请求体校验的 400 处理器捕获；路径参数在端点内按字符串接收并解析。

## 错误信息边界

- 拒绝说明面向业务用户，按拒绝码给出稳定文案（如「仅允许单条只读 SELECT 查询」），不回显内部判定细节。
- 执行错误映射为稳定摘要（错误码 + 简述），不透传 PostgreSQL 原始错误文本与堆栈；未映射错误给通用文案。理由：用户可见文案不随数据库版本漂移，且最小化信息暴露。原始摘要入审计记录，供开发排查。
- 拒绝码、错误码与文案集中在 API 一处定义，策略与执行模块只产生码，不产生文案。

## 行数上限语义

**决策：达到行数上限即停止取数并标记截断**——响应 `truncated: true`，`row_count` 等于实际返回行数（即上限），审计记录同步标记。理由：业务用户拿到带显式截断标记的部分结果，优于整单失败；固定数据集下大结果集是合理场景（如全量明细），截断标记保证不会被误读为完整结果。前端展示截断提示（见 [workbench.md](workbench.md)）。
