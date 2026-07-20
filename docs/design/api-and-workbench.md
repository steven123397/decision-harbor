# API 与查询工作台设计

## 范围

首轮最小 HTTP 接口的响应结构、错误语义，以及最小查询工作台的行为。端点集合以 [产品需求](../background/product-requirements.md) 的"最小外部 API"为准，本文定义其契约细节。

## 端点与响应

统一约定：业务响应为 JSON；非业务错误（请求非法、记录不存在、未预期异常）使用错误信封：

```json
{ "error": { "code": "INVALID_REQUEST", "message": "..." } }
```

### `GET /health`

仅检查进程存活，不触数据库。`200` → `{ "status": "ok" }`。

### `GET /ready`

仅当 `platform` 与 `analytics` 均可连接且迁移位于 head 时 `200` → `{ "status": "ready" }`；否则 `503` → `{ "status": "not_ready" }`。

### `POST /api/v1/query-runs`

请求体：`{ "sql": "..." }`，必须为非空字符串，否则 `400` + `INVALID_REQUEST`（不产生审计记录——请求未进入查询运行生命周期）。

提交被处理后一律返回 `200`，统一结构：

```json
{
  "record": {
    "id": "…",
    "status": "succeeded | rejected | failed",
    "sql": "…",
    "error_code": null,
    "error_message": null,
    "row_count": 3,
    "duration_ms": 12,
    "created_at": "…"
  },
  "result": {
    "columns": [{ "name": "region", "type": "varchar" }],
    "rows": [["East"]],
    "truncated": false
  }
}
```

- `result` 仅在 `succeeded` 时非空；`columns` 给出列名与类型名，`truncated` 标记行数上限截断。
- `rejected` / `failed` 时错误码与说明在 `record.error_code` / `record.error_message`。

决策（外部行为）：策略拒绝与执行失败返回 `200` 而非 4xx。理由：它们是产生审计记录的正常业务结果，不是协议错误；客户端只需按 `record.status` 三分支处理，统一结构因此真正统一。HTTP 状态码只表达传输与请求层结果（`400` / `404` / `503`）。

### `GET /api/v1/query-runs/{id}`

`200` → `{ "record": { … } }`，仅审计事实，不含结果行（结果不持久化，见 [总体设计](overview.md)）。未知 id → `404` + `RECORD_NOT_FOUND`。

## 错误码契约

稳定码全集：`INVALID_REQUEST`、`RECORD_NOT_FOUND`、`POLICY_INVALID_SYNTAX`、`POLICY_MULTI_STATEMENT`、`POLICY_NON_QUERY_STATEMENT`、`POLICY_WRITE_OPERATION`、`POLICY_UNAUTHORIZED_OBJECT`、`QUERY_TIMEOUT`、`EXECUTION_ERROR`、`INTERNAL_ERROR`。新增可追加，既有码不得改义。

## 查询工作台

单页最小工作台，无历史列表、无图表（背景非目标）：

- SQL 输入框与提交按钮；提交后禁用输入并显示"执行中"，响应到达后恢复。
- `succeeded`：结果表格（列头 + 行），附行数、耗时；`truncated` 时显示截断提示。
- `rejected`：拒绝面板，展示错误码、可读说明与记录 id。
- `failed`：失败面板，展示错误码、错误摘要与记录 id。
- 初始加载前可用 `GET /ready` 指示后端就绪状态。

实现形态：React 19 + TypeScript，Vite 构建静态资源，nginx 托管并反向代理 API（见 [总体设计](overview.md) 关键决策）。

## 测试接缝

- Vitest：工作台各渲染分支（执行中、结果表、截断提示、拒绝与失败面板）与响应映射。
- Playwright：浏览器主流程（输入、提交、执行中、结果或拒绝），见 [测试设计](testing.md)。
