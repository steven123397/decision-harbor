# 查询记录与最小 API

## 范围

本文定义查询记录（query run）的状态机与审计事实、`platform` 库的审计表结构、对外 HTTP 端点的请求响应语义和稳定错误码注册表。端点集合的权威来源是 [产品需求](../background/product-requirements.md) 的"最小外部 API"一节。

## 状态机

```text
running ──> succeeded
        ──> rejected
        ──> failed
```

- `running`：记录已创建、判定或执行尚未结束的过渡态。同步执行下正常请求结束时必达终态；崩溃遗留的 `running` 是中断证据（见 [architecture.md](architecture.md)）。
- `succeeded` / `rejected` / `failed`：终态，语义见 [CONTEXT.md](CONTEXT.md)。终态不可再变更。

## 审计事实与表结构

`platform` 库表 `query_runs`，由 Alembic 迁移建立：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `uuid` 主键 | 应用生成 UUIDv4。不用自增整数，避免对外可枚举审计记录、且跨工作区实例无冲突 |
| `sql_text` | `text` 非空 | 用户原始 SQL，逐字保存 |
| `status` | `varchar(20)` 非空 | 状态机取值 |
| `error_code` | `varchar(50)` 可空 | 拒绝或失败的稳定错误码 |
| `error_message` | `text` 可空 | 错误摘要：取首行、截断至 500 字符，不含连接串或堆栈 |
| `row_count` | `integer` 可空 | 成功时实际返回的行数 |
| `truncated` | `boolean` 非空，默认 `false` | 结果是否因行上限被截断 |
| `duration_ms` | `integer` 可空 | 从开始执行到取回结果的耗时 |
| `created_at` | `timestamptz` 非空 | 记录创建时间 |

该表覆盖产品需求列举的全部审计事实（原始 SQL、判定或拒绝原因、执行状态、行数、耗时、错误摘要、创建时间）。策略通过不单设字段：状态进入执行即表明判定为允许。

## 端点语义

### `GET /health`

进程存活即 `200 {"status": "ok"}`，不触库。

### `GET /ready`

平台库与分析库均可连接、且两套迁移都处于最新版本时返回 `200`；否则 `503` 并说明未就绪原因。供统一启动命令与编排等待使用。

失败必须有界：数据库完全不可达（连接被静默丢弃）时也要在有限时间内返回 `503`，不得挂起——挂起会让编排的就绪等待既拿不到结论也拿不到失败。上界由连接超时给出，见 [local-runtime.md](local-runtime.md) 的 `DB_CONNECT_TIMEOUT_S`。未就绪原因只写库别名与错误类型，不含连接串或口令。

### `POST /api/v1/query-runs`

请求体 `{"sql": "..."}`。处理流程见 [architecture.md](architecture.md) 数据流。

**HTTP 状态码语义**：记录成功创建即返回 `201`，无论查询结果是 `succeeded`、`rejected` 还是 `failed`——拒绝与执行失败是受治理链路的正常业务结论，而非协议错误。`4xx` 只用于请求本身不合法（缺 `sql`、非字符串、超长），此时不创建记录。理由：让"策略拒绝"成为可依赖的一等结果，客户端无需在 HTTP 错误处理里解析业务语义；超长输入不入审计，防止审计存储被恶意大输入撑爆。

成功响应（`201`）：

```json
{
  "query_run": {
    "id": "…",
    "status": "succeeded",
    "row_count": 2,
    "truncated": false,
    "duration_ms": 12,
    "error": null,
    "created_at": "2026-07-26T08:00:00Z"
  },
  "result": {
    "columns": [{"name": "region", "type": "varchar"}],
    "rows": [["East"], ["North"]]
  }
}
```

拒绝或失败响应（同为 `201`）：`result` 为 `null`，`query_run.error` 为 `{"code": "…", "message": "…"}`。

值序列化规则：`numeric` 一律序列化为字符串，保持定点语义、避免 JSON 浮点失真（契约要求金额定点）；时间戳为 ISO 8601 字符串；`NULL` 为 `null`。列的 `type` 取 PostgreSQL 类型名。

### `GET /api/v1/query-runs/{id}`

返回 `200` 与 `{"query_run": {…}}`，字段同上并额外含 `sql_text`；不含结果行（结果不落库，见 [architecture.md](architecture.md) 关键决策）。无此记录返回 `404`。

请求级错误响应统一为 `{"error": {"code": "…", "message": "…"}}`。

## 错误码注册表

全部对外错误码在此注册；新增只能追加，已发布的码不改语义、不复用：

| 错误码 | 场景 | 出现位置 |
| --- | --- | --- |
| `invalid_request` | 请求体不合法或 `sql` 超长 | HTTP 422 |
| `not_found` | 查询记录不存在 | HTTP 404 |
| `policy_parse_error` | SQL 不可解析 | `rejected` |
| `policy_multiple_statements` | 多条语句 | `rejected` |
| `policy_forbidden_statement` | 非只读语句形态 | `rejected` |
| `policy_forbidden_feature` | `SELECT INTO`、锁定子句、非内建类型的转换目标等被禁特性 | `rejected` |
| `policy_forbidden_object` | 越出允许对象范围 | `rejected` |
| `policy_forbidden_function` | 调用安全允许集之外的函数或系统信息表达式 | `rejected` |
| `execution_timeout` | 触发语句超时 | `failed` |
| `execution_error` | 其他执行期数据库错误 | `failed` |
| `internal_error` | 平台自身故障（含审计写入失败） | HTTP 500 |

策略类错误码的触发规则见 [query-governance.md](query-governance.md)。

## 失败与迁移

- 审计写入失败时请求以 `internal_error` 失败，不返回查询结果：可审计性优先于可用性，避免产生无记录的成功查询。
- 表结构演进（如新增用户字段）通过追加列的 Alembic 迁移完成，不重解释既有列。

## 测试接缝

集成测试基于真实双库环境（pytest + Compose）：

- 身份边界：`platform_app` 无法连接 `analytics`；`analytics_reader` 无法连接 `platform`、对契约表只能 `SELECT`、写入尝试被库层拒绝。
- 执行限制：`pg_sleep` 类慢查询触发 `execution_timeout`；超行数查询返回截断标记且行数等于上限。该函数不在策略允许集内（见 [query-governance.md](query-governance.md)），因此超时在执行器接缝上直接验证，不经端到端路径。
- 端到端：提交合法查询 → `201` + `succeeded` + 结果与 seed 数据一致；提交被禁 SQL → `rejected` + 对应错误码；每种请求各自留下状态正确的审计记录，可经 `GET` 读回。
- 系统目录绕过：两条已知路径（函数参数走私系统 SQL、内层 CTE 遮蔽物理表）以稳定错误码 `rejected`，且 `result`、`row_count`、`duration_ms` 均为空——没有行数与耗时即"未进入执行"的证据。SQL 见 `api/conftest.py`。
- 幂等与就绪：`/ready` 在迁移完成前后行为正确；数据库不可达时在连接超时上界内返回未就绪原因，且原因中不含连接串。
