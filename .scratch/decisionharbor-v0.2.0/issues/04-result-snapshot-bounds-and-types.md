# 04 — 结果快照的行数、字节与类型边界

**What to build:** 结果快照始终是可预测大小的不可变前缀。它同时受行数和字节数约束，越界时保存最长稳定前缀并明确标注截断，而不是失败；列定义或单行本身超出字节预算时，查询运行以 `result_too_large` 失败，且不保存部分行或部分单元格。所有值按显式 JSON 类型稳定序列化，使客户端无需猜测类型。

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] 快照最多保存 500 行；恰好 500 行不标记为截断，第 501 行存在时截断且只保存前 500 行
- [ ] 字节预算为 1 MiB 即 1,048,576 字节，按 `{"columns":[...],"rows":[...]}` 的紧凑 UTF-8 JSON 计算：键顺序固定为 `columns`、`rows`，每个列对象固定为 `{"name":string,"type":string}`，每行是与列位置对应的 JSON 数组，分隔符不含空格，非 ASCII 字符直接编码为 UTF-8
- [ ] envelope、运行元数据和 `truncated` 不计入字节预算
- [ ] 完整快照恰好等于 1 MiB 时成功保存；超出 1 字节时截断保存最长前缀并记录 `truncated`
- [ ] 列定义本身超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`，不保存部分内容
- [ ] 第一行加入后使快照超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`
- [ ] 任意单行的 JSON 数组本身超过 1 MiB 时运行进入 `failed`，错误码 `result_too_large`
- [ ] 其他累计越界只保存不超过行数与字节数双重边界的最长前缀，并记录 `truncated`
- [ ] 多字节 UTF-8 文本按 UTF-8 字节数计入预算，而不是字符数
- [ ] `bigint` 与 `numeric` 序列化为十进制字符串，日期与时间戳为 ISO 8601 字符串，布尔值、32 位整数、文本与 `null` 使用 JSON 原生类型
- [ ] 未知结果类型使运行以 `unsupported_result_type` 失败
