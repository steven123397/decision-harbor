# 06 — 查询工作台接通轮询与结果

**What to build:** 业务用户在浏览器中提交 SQL 后不必保持一个长时间请求。工作台自行轮询，在排队、运行与成功之间自然推进，成功时展示列、行和截断提示；刷新页面后按运行标识恢复同一查询运行的状态与结果，浏览器会话不再是查询生命周期的所有者；策略拒绝与执行失败在界面上被清晰区分，让用户知道下一步该改 SQL 还是重试。

**Blocked by:** 03, 05

**Status:** resolved

- [x] 提交后界面依次反映排队与运行状态，不再显示为一个不确定的长时间等待
- [x] 成功运行展示结果列、行与截断提示，并显示行数与耗时等审计事实
- [x] 刷新页面后按运行标识恢复同一查询运行的状态与结果
- [x] 策略拒绝与执行失败使用不同的标题、图标与错误码说明，用户能判断下一步
- [x] 结果尚未就绪时给出明确的等待反馈，不误导为失败
- [x] 结果已过期或不可用时给出可区分的反馈与下一步提示
- [x] 轮询在终态、页面卸载和不可恢复错误后停止
- [x] 同一查询运行不产生重叠的轮询请求
- [x] 使用轮询实现，不引入 SSE 或 WebSocket

## Resolution

**验收证据：**

- 排队→运行→成功（单元，`apps/web/src/runTracking.test.tsx`）：假定时器逐轮推进 `getRun`（`queued`→`running`→`succeeded`），断言 `Queued`、`Running`、`Query succeeded` 依次出现，前两轮没有表格，第三轮出现列、行、`2 rows`、`14 ms`、`Result truncated` 和运行标识；同一用例断言 `getRun` 恰好 3 次、`getResult` 1 次。
- 同一路径（浏览器，`apps/web/e2e/workbench.spec.ts`「polls a queued run and shows its result table」）：真实 Worker 与 Chromium 上点击一次 Run query，1.7s 内走到 `Query succeeded`，表格首行含 `East`，审计事实含 rows 与 ms，且地址栏出现 `run=<uuid>`。
- 刷新恢复（单元 + 浏览器）：单元用例先 `replaceState` 到 `/?run=<uuid>` 再渲染，断言编辑器被回填为该运行的 SQL、结果表格与运行标识出现、且 `runQuery` 一次都没被调用；e2e 在一次成功运行后 `page.reload()`，仍读到同一结果、同一 `run` 参数，并断言编辑器含 `revenue`。
- 拒绝与失败（单元 + e2e）：两条单元用例分别断言 `Query rejected`＋`sql_object_not_allowed`＋`.error-rejected`＋「Edit the SQL…」，以及 `Execution failed`＋`query_timeout`＋`.error-failed`＋「passed the policy check…」，两者互不出现；e2e 用 `DELETE FROM customers` 证明策略拒绝得到 `Query rejected` 与 `sql_statement_not_allowed` 且没有表格。
- 未就绪不是失败（单元）：排队中的等待卡片带「The result is not ready yet — this page keeps polling.」且断言 `Execution failed` 不存在；`result_not_ready` 用例断言成功后先显示「Reading the result snapshot…」，下一轮才出现表格，全程没有失败面板。
- 过期与不可用（单元 + e2e）：两条单元用例分别断言 `Result no longer retained`＋`result_expired` 与 `No result to read`＋`result_unavailable`，互相排除，且都给出「Run the query again…」；e2e 用 `page.route` 拦截 `**/api/v1/query-runs/*/result` 返回 410 与 409，在真实浏览器里复现同一界面。
- 停止条件（单元）：终态后推进 10 个间隔 `getRun` 仍为 1 次；`unmount()` 后推进 5 个间隔不再调用；`getRun` 返回 `query_run_not_found` 后停止并显示 `Query run not available`；`getResult` 返回 `query_run_not_found` 同样停止（review 发现后补的路径）。
- 不重叠（单元）：`getRun` 返回永不 settle 的 promise，推进 5 个间隔后仍只被调用 1 次，且每次实参都是同一运行标识；另有一条用例证明第二次提交后只轮询新的运行标识，地址栏同步更新。
- 无 SSE/WebSocket：`grep -rn "EventSource\|WebSocket" apps/web/src` 无命中，客户端只有 `fetch` 轮询。
- 回归：`./dev test` 全绿（API 249 通过 1 条库告警、Worker 32、Web 44、e2e 7，退出码 0）；`datasets/sales-analytics-v1` 的 `python3 validate.py` 通过；`npx tsc -b` 无输出；`git diff --check` 无空白错误。

**主要实现：**

- `apps/web/src/api.ts`：`ApiClient` 增加 `getRun()` 与 `getResult()`，三个端点共用 `request()` 这一个 envelope 读取点；传输失败统一收敛为客户端错误码 `network_error`（不再冒充 `service_not_ready`）；`QueryRun` 补上 `raw_sql`，供恢复时回填编辑器。
- `apps/web/src/runTracking.ts`（新增）：`useTrackedRun()` 串行轮询一个查询运行——下一轮只在上一轮 settle 后调度，`inFlight` 兜住重叠；终态、组件卸载以及 `query_run_not_found`/`result_expired`/`result_unavailable` 之后停止；`succeeded` 之后只读一次结果快照；`readTrackedRunId()`/`rememberTrackedRunId()` 用地址栏 `run` 参数承载恢复入口，非 UUID 形状的值直接忽略，避免对着垃圾标识无限轮询。
- `apps/web/src/App.tsx`：视图按运行事实分派——`restoring`/`pending`（排队、运行、取消中）、`success`（表格＋截断提示＋审计事实，或结果不可用/已过期的说明面板）、`terminal`（策略拒绝、执行失败、已取消三种不同结局）、`missing`；提交未产生运行的事实用单独标题 `Query not started` 表达，不再混称「执行失败」。
- `apps/web/tests/setup.ts`：显式打开 `IS_REACT_ACT_ENVIRONMENT`（本套件关掉 `globals`，RTL 不会替我们打开它，而手推定时器必须在 `act` 内收口）。
- `docs/adr/0004`：补记客户端如何消费快照（按运行标识轮询、不引入 SSE/WebSocket、三种结果语义在界面上是三种结局）。

**Review 结论：** `/code-review` 双轴复核共 12 项。已处理：结果读取路径漏掉 `query_run_not_found` 的停止条件（与本次自己写进 ADR 的断言矛盾，已补并加用例）；提交未受理却标成「Execution failed」，与 CONTEXT.md 的执行失败定义冲突，改为 `Query not started` 并更新用例；`ResultProblem` 与错误码正反各写一遍改为直接保存 API 错误码；`notice: 'reconnecting' | null` 改为布尔 `reconnecting`；`settled` 与派生初值双源 state 收敛为 `track()` 一次性写入 `progress`；`intervalMs` 无人传入的参数删除；`ResultProblemState` 与 `MissingState` 合并为 `NoticeState`；CSS 里 `.error-*` 与 `.notice-*` 五个同构块合并、重复的 `.reconnecting-notice` 规则删掉；`PENDING_COPY.received` 与 `queued` 文案重复，删掉 `received` 让它落到兜底。按有意边界保留：刷新时回填原始 SQL（AC3 的「恢复状态」包含让结果有可对照的查询，否则页面会展示一段看不见的 SQL 的结果）；渲染 `cancelled` 终态卡片（轮询会读到该终态，不渲染就是空白屏，取消操作本身仍属 12）；客户端自造 `network_error`（服务端不会返回它，需要区别于 503 `service_not_ready`）；`IS_REACT_ACT_ENVIRONMENT` 写进测试 setup（本仓库关掉 vitest `globals`，RTL 的自动注册不生效）。

**保留风险与后续票据：**

- 传输失败与 5xx 仍按可恢复处理并持续轮询，只有 404/410/`result_unavailable` 视为不可恢复；服务长时间不可用时界面停在 `Reconnecting`，不会自行放弃。
- 轮询间隔固定 1000 ms，既不可配置也没有退避；spec 未规定间隔，长时间等待的运行会持续每秒一个 `GET`。
- `result_not_ready` 等可恢复的结果读取失败没有重试次数上限，只是继续轮询。
- 回填 SQL 只在「从地址栏恢复」的第一个已读运行上执行一次；用户在恢复期间编辑 SQL 不会被覆盖，但下方仍展示恢复运行的事实。
- 前端不发送 `Idempotency-Key`，因此 `idempotency_conflict` 在浏览器侧不可达；若响应同时带 error 与 `query_run`，当前按「跟随该运行」处理（策略拒绝正是这种形状，必须跟随）。
- 取消操作、重试、历史浏览、窄视口与键盘可访问性属 12；窄视口无内容重叠未在本票做系统验证。
- 只验证了一个 Worker 副本的默认路径；租约、容量与接管属 07，届时界面无需改动即可跟随新的执行尝试。

**提交 SHA：** `41a6abc`（分支 `v0.2.0/codebuddy-hy4-preview-high`，未推送；本行由随后的 tracker 关闭提交写入，实现提交本身不含自身 SHA）。

**本票关闭后 frontier 为：** 07（租约、容量与接管，blocker 03 已解决）、11（运行历史分页，blocker 02 已解决）。12 仍被 09、10、11 阻塞。
