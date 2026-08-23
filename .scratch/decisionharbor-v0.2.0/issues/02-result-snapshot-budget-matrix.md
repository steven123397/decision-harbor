# 02 — 结果快照大小预算与截断矩阵

**What to build:** 平台以可复现的方式执行 spec 定义的结果快照预算：最多 500 行且紧凑 JSON 不超过 1 MiB（1,048,576 字节）。正常累计越界保留数据库返回顺序的最长合法前缀并记录 `truncated`；结构性越界（列定义本身过大、第一行加入后即超限、任意单行数组本身超限）使运行以 `result_too_large` 失败且不保存任何部分结果。用户读到的快照值继续遵循 ADR-0007 的显式 JSON 类型。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** resolved

- [x] 字节数按 spec 紧凑 JSON 口径计算：固定键序 `columns`、`rows`；列对象固定 `{"name","type"}` 字段序；每行是与列位置对应的 JSON 数组；分隔符不含空格，非 ASCII 字符直接编码为 UTF-8；envelope、运行元数据与 `truncated` 不计入预算
- [x] 完整快照恰好 1 MiB 时成功保存；恰好 500 行时成功；两类边界各超出 1 个单位（1 字节 / 1 行）时按行数与字节数双重边界截断并记录 `truncated`
- [x] 列定义本身超过 1 MiB、第一行加入后使快照超过 1 MiB、任意单行 JSON 数组本身超过 1 MiB：运行进入 `failed`，错误码 `result_too_large`，不保存部分行或部分单元格
- [x] 多字节 UTF-8 内容正确计入预算，保存后可无损读回
- [x] 快照值类型遵循 ADR-0007：bigint/numeric 为十进制字符串，日期与时间戳为 ISO 8601 字符串，布尔、32 位整数、文本与 null 为 JSON 原生类型；未知类型以 `unsupported_result_type` 失败
- [x] 截断保留数据库返回顺序的稳定前缀；提取继续走服务端游标有界读取，不先完整物化再截断
- [x] 终态 `succeeded` 可见时快照必可读（原子性证据）；`result_too_large` 失败路径不留下可读快照
- [x] 预算计算的边界用例有快速单元测试；500 行与 1 MiB 边界在真实数据库上有集成测试

## Resolution

**结果：** 交付完整的结果快照大小预算与截断矩阵，并把提取路径改为预算内增量计算。

- `snapshots.py` 重写为增量 `SnapshotBuilder`：列对象与行的紧凑 JSON 字节逐行累计，`add_row` 在行数或字节预算耗尽时返回 False 并固定最长合法前缀；列定义超限、首行加入后整体超限、单行本身超限抛 `SnapshotTooLarge`。`build_snapshot(columns, rows)` 保留为纯函数接缝。
- `executor.py` 新增 `extract_snapshot` 接缝：游标 `fetchone` 逐行提取并同步累计预算，预算耗尽立即停止读取（不再 `fetchmany(max_rows+1)` 先物化再截断）；`SnapshotTooLarge` 映射为 `result_too_large`，列/单元格未知类型保持 `unsupported_result_type`。`execute` 直接返回 `BuiltSnapshot`，`QueryResult` 从 domain 移除。
- `worker.py` 简化：`publish_success` 直接持久化执行器产出的快照；`result_too_large` 走统一 `publish_failure` 条件更新。
- 测试：单元层覆盖紧凑 JSON 字节口径、恰好 1 MiB / 500 行与 +1 单位双边界、三类结构性越界、多字节计数、ADR-0007 类型矩阵、提取提前停止（fetch 桩计数证明 600 行只读 2 行）；真实数据库集成层（`test_snapshot_budget.py`）覆盖恰好 1 MiB 成功、+1 字节稳定前缀截断、恰好 500 行 / 501 行截断、首行超限与单行超限 `result_too_large` 且无部分快照、多字节无损读回、timestamptz/date ISO 8601、Worker 原子发布与 API 截断结果读取。
- ADR-0006 证据段刷新为 `fetchone` + 增量预算的现行实现；决策本身不变。

**实际验证：** `API_HOST_PORT=18000 WEB_HOST_PORT=15173 COMPOSE_PROJECT_NAME=decisionharbor-zcode-glm53-xhigh ./dev test` 全绿——api-test 144 通过（含 12 项快照预算矩阵集成证据）、web-test 33 通过、e2e 4 通过，退出码 0；`datasets/sales-analytics-v1` `validate.py` 通过；`git diff --check` 干净。

**Review：** `/code-review` 双轴复核。Standards：无硬性违规；修复了三处判断项（`unsupported_result_type` 错误消息去重、集成测试 `worker_settings`/清理 fixture 抽入 `tests/integration/conftest.py`、变量重命名），ADR-0006 陈旧证据已刷新。Spec：全部验收项有对应实现与测试证据；补充了列定义超限的 `extract_snapshot` 接缝测试（真实 PostgreSQL 标识符/列数上限使该形态在端到端不可达）和 timestamptz/date 的真实数据库 ISO 8601 证据。有意保留：`build_snapshot` 纯函数接缝（测试专用）、`SnapshotBuilder` 的 `max_rows` 非正值防御（配置层已约束，防御深度保留）。

**保留风险：** `result_too_large` 映射后的自动尝试策略消费在 05；24 小时保留期与清理在 03。
