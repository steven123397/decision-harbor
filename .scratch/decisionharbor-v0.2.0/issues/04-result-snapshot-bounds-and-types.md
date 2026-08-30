# 04 — 结果快照的行数、字节与类型边界

**What to build:** 结果快照始终是可预测大小的不可变前缀。它同时受行数和字节数约束，越界时保存最长稳定前缀并明确标注截断，而不是失败；列定义或单行本身超出字节预算时，查询运行以 `result_too_large` 失败，且不保存部分行或部分单元格。所有值按显式 JSON 类型稳定序列化，使客户端无需猜测类型。

**Blocked by:** 03

**Status:** resolved

- [x] 快照最多保存 500 行；恰好 500 行不标记为截断，第 501 行存在时截断且只保存前 500 行
- [x] 字节预算为 1 MiB 即 1,048,576 字节，按 `{"columns":[...],"rows":[...]}` 的紧凑 UTF-8 JSON 计算：键顺序固定为 `columns`、`rows`，每个列对象固定为 `{"name":string,"type":string}`，每行是与列位置对应的 JSON 数组，分隔符不含空格，非 ASCII 字符直接编码为 UTF-8
- [x] envelope、运行元数据和 `truncated` 不计入字节预算
- [x] 完整快照恰好等于 1 MiB 时成功保存；超出 1 字节时截断保存最长前缀并记录 `truncated`
- [x] 列定义本身超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`，不保存部分内容
- [x] 第一行加入后使快照超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`
- [x] 任意单行的 JSON 数组本身超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`
- [x] 其他累计越界只保存不超过行数与字节数双重边界的最长前缀，并记录 `truncated`
- [x] 多字节 UTF-8 文本按 UTF-8 字节数计入预算，而不是字符数
- [x] `bigint` 与 `numeric` 序列化为十进制字符串，日期与时间戳为 ISO 8601 字符串，布尔值、32 位整数、文本与 `null` 使用 JSON 原生类型
- [x] 未知结果类型使运行以 `unsupported_result_type` 失败

## Resolution

**结果：** 全部验收条件完成。结果快照现在同时受 500 行与 1 MiB 约束：越界时保存数据库顺序下的最长前缀并记录 `truncated`，列定义、首行或任意单行本身超出预算时运行以 `result_too_large` 失败且不落库任何部分内容。

**实际验证（在 `./dev up` 起栈的本地 Compose 环境上，沿用既有 postgres 数据卷，未执行 `./dev destroy`）：**

- `./dev test` 退出码 0：`tests/unit` + `tests/integration` 198 passed，`tests/worker` 27 passed，Vitest 27 passed，Playwright 3 passed。
- `python3 validate.py`：`datasets/sales-analytics-v1` 校验通过；`git diff --check` 无输出。
- 行数边界：worker 测试用 `SELECT id FROM order_items ORDER BY id LIMIT 500` 得到 500 行且 `result_truncated=False`；`LIMIT 501` 得到 500 行、`result_truncated=True`，落库行恰为 `1` 到 `500`。
- 字节边界：单列 `text` 的一行 `repeat('x', 1_048_518)` 使快照恰好 1,048,576 字节并成功保存；两行各 `repeat('x', 524_257)` 时第 1 行占 524,315 字节、两行合计 1,048,577 字节，于是只保存第 1 行且 `truncated=True`。
- 失败路径：`repeat('x', 1_048_519)` 使首行加入后越界、`repeat('x', 1_048_576)` 使单行 JSON 数组本身越界、以及「首行 10 字节 + 第 2 行 1,048,576 字符」三种查询都得到 `failed` + `result_too_large`，且 `query_run_results` 中没有记录。
- 公开提交路径：integration 测试经 `POST /api/v1/query-runs` 提交策略允许的 1663 列宽结果（`SELECT o.order_no AS c0, ... FROM orders o ORDER BY o.id`），运行收敛为 `succeeded`、`returned_row_count=34` 且 `result_truncated=True`；落库快照按合同形式重算不超过 1 MiB，补上第 35 行即越界。
- 多字节：`repeat('数', 100000)` 的每行 JSON 数组是 300,004 字节，3 行后为 900,068 字节，第 4 行会到 1,200,073 字节，因此只保存 3 行；按字符数计则可放 10 行。
- 类型：落库快照里 `bigint`/`numeric` 是十进制字符串，timestamptz 是 `...+00:00` 的 ISO 8601 字符串，`date` 与 timestamp 同日，`integer` 是 JSON number，`boolean` 是 JSON bool，`char(3)` 是文本，`segment` 允许 `null`。

**主要实现：**

- `domain.py`：`RESULT_MAX_ROWS=500`、`RESULT_MAX_BYTES=1_048_576`、`encode_json`（紧凑 UTF-8、非 ASCII 不转义）、`encode_snapshot_columns`、`ResultTooLarge` 与 `ResultSnapshotBuilder`。builder 构造时先给「只有列定义的空快照」定价，越界即抛；`add()` 先判行上限再判单行大小，最后判累计字节，被拒即置 `truncated`。
- `executor.py`：`min(max_rows, RESULT_MAX_ROWS)` 决定行上限，循环以 `FETCH_BATCH_SIZE=64` 分批 `fetchmany(row_limit - row_count + 1)`，前缀确定即停止读取；`ResultTooLarge` 映射为 `result_too_large` 的 `ExecutionFailure`，游标与事务照既有路径收尾。
- `worker/queue.py`：`publish_success` 改用 `encode_snapshot_columns` 与 `encode_json` 落库，与计量共用同一编码。
- 三份 ADR 的 `Implementation and evidence` 同步：0004 记双重边界与证据，0006 记分批提取替代单次 `fetchmany(max_rows + 1)`，0007 记落库沿用同一显式类型表示。

**Review 结论：** `/code-review` 双轴复核共 5 项，4 项已处理、1 项按有意边界保留。已处理：删除未使用的 `max_bytes` 形参（Speculative Generality）；抽出 `encode_snapshot_columns`，让计量与落库共用列定义编码而不是各写一份（Duplicated Code）；三个测试文件的字节预算常量改为同用 `RESULT_MAX_BYTES`，`snapshot_bytes` 语义统一为返回字节（命名不一致）；补齐等号边界测试（空快照恰好 1 MiB 与超出 1 字节、单行数组恰好 1 MiB 与超出 1 字节）。Spec 轴还发现一处实质问题：`add()` 原本先判单行大小再判行上限，会把「500 行之后那个只用于证明结果仍在继续的越界行」变成整run失败，与验收第 1 条冲突；已调换顺序，并补两条单测与一条 worker 测试。按有意边界保留：列定义按「只有列定义的空快照」定价而不是裸列数组，否则零行结果的落库快照会大于 1 MiB；理由写进 `ResultSnapshotBuilder` 的 docstring。

**保留风险与后续票据：**

- 结果读取语义、24 小时保留期与幂等清理属 05；在此之前快照只能经直连 platform 库校验，公开 HTTP 上仍读不到 `truncated` 与行列内容。
- 「列定义本身超过 1 MiB」在真实 analytics SQL 下不可达：PostgreSQL 把输出列名截断到 63 字节，目标列最多 1664 项，列定义 JSON 上限约 186 KB。该分支只有契约级（单元）证据。
- 字节预算在策略允许的 SQL 下只能靠宽目标列触发：`||`、`concat`、`substring` 都不在策略允许集合内，单列文本无法被放大到 1 MiB。worker 测试因此用 `repeat()` 等策略不允许的 SQL 直插队列走 Worker 执行接缝，与既有 `test_query_run_lifecycle.py` 的 `FOR UPDATE` 用例同一做法。
- 500 行上限在 executor 侧取 `min(run.max_rows, 500)`；`QUERY_MAX_ROWS` 仍可配到 5000，但快照永不保存超过 500 行，多取的行不再读取。
- `FETCH_BATCH_SIZE=64` 只决定每批读取行数，不是并发上限；全局并发控制仍属 07。

**提交 SHA：** `dbd5bd8`（分支 `v0.2.0/codebuddy-hy4-preview-high`，未推送；本行由随后的 tracker 关闭提交写入，实现提交本身不含自身 SHA）。

**本票关闭后 frontier 仍为：** 05（结果读取语义与清理）、07（租约、容量与接管）、11（运行历史分页），三者都只依赖已解决的 01/02/03；13 还等 05、08、12。
