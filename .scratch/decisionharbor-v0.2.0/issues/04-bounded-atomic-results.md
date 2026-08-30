# 04 — 原子发布精确受限的结果快照

**What to build:** 让成功查询只保存可预测大小的稳定结果前缀，并确保只有当前执行所有者能够把结果与成功终态一起发布。任何无法形成合法有限快照的查询都稳定失败，而不是保存含糊或部分单元格。

**Blocked by:** 03 — 协调双 Worker 的全局执行容量

**Status:** resolved

- [x] 结果快照最多保存 500 行和 1,048,576 字节，并按规范规定的紧凑 UTF-8 JSON、固定键顺序和列字段顺序精确计量。
- [x] 恰好达到行数或字节边界的快照可以成功；累计越界时只保存同时满足两个边界的最长有序前缀并标记 `truncated`。
- [x] 列定义超限、加入第一行即超限或任意单行自身超限时进入 `failed/result_too_large`，不保存部分行、部分单元格或结果内容。
- [x] 多字节 UTF-8、后续单行过大以及超出边界 1 字节均按同一计量规则处理，并由确定性单元测试覆盖。
- [x] `bigint`、`numeric`、日期时间、布尔值、32 位整数、文本和 `null` 保持既有显式 JSON 表示；未知类型以 `unsupported_result_type` 失败。
- [x] 成功终态和结果快照由当前状态、执行尝试及 generation 条件保护并在单一事务中发布；条件不匹配的发布不产生状态或结果写入。
- [x] Worker 使用服务端游标增量提取，不先完整物化结果；成功、失败、超限或所有权失效后都关闭游标并结束事务，使连接回到可复用状态。
- [x] 真实 PostgreSQL 证据覆盖原子成功、发布条件失效、精确大小边界、稳定截断前缀和无部分快照失败路径。

## Resolution

Ticket 04 已交付精确受限、原子发布的结果快照。`result_snapshot.py` 是紧凑 JSON 编码和大小计量的唯一边界；Worker 通过命名服务端游标逐行提取，并只保留同时满足 500 行与 1 MiB 的最长有序前缀。列定义、首行或任意已读取单行超限时，运行稳定进入 `failed/result_too_large` 且不保存结果内容；未知类型继续以 `unsupported_result_type` 失败。

成功、失败和续租均匹配当前运行状态、generation、Worker、未过期租约及未释放 execution attempt。成功终态、attempt 释放和结果插入位于同一 platform 事务；任何发布条件失效或结果插入失败都不会留下新的终态或部分快照。

### 验收证据

- `apps/api/tests/unit/test_executor.py`：覆盖规范 JSON 编码、恰好 500 行与第 501 行、恰好 1 MiB、多字节 UTF-8、超出 1 字节、累计越界、列定义超限、首行及后续单行超限，并回归显式结果类型。
- `apps/api/tests/integration/test_worker_repository.py`：每条 ownership 测试使用迁移到 Alembic head 的一次性 platform PostgreSQL；真实 analytics 游标覆盖精确字节边界、稳定前缀、失败后连接复用、未知类型、无结果失败、旧 generation、已释放 attempt，以及结果插入失败时的整事务回滚。
- `env COMPOSE_PROJECT_NAME=decisionharbor-ticket04-codex API_HOST_PORT=18104 WEB_HOST_PORT=15174 ./dev test`：review 修复后的最终运行通过 138 个 API 测试、33 个 Web 测试和 4 个 Playwright 浏览器测试。
- 在 `datasets/sales-analytics-v1/` 中运行 `python3 validate.py`：固定数据集校验通过；`python3 -m compileall -q apps/api/src apps/api/tests`、Web `npm run build` 与 `git diff --check` 通过。仓库未配置 Python 静态类型检查器，因此没有把 `compileall` 描述为类型检查。

### Review

- 首轮 Standards 提示快照编码和 ownership SQL 谓词存在判断性的重复；ADR 证据段落曾因未纳入更具体的 `docs/adr/README.md` 被误报为硬违规。Spec 轴无发现。
- `d50a5fc` 抽取唯一结果快照编码模块及共享 ownership/attempt SQL 谓词；复核依据 ADR profile 撤回误报。最终 Standards 与 Spec 均为 0 个发现。

### 提交

- `3c12bcd`：实现精确快照计量、增量提取、原子发布、真实 PostgreSQL 证据和稳定 Web 错误反馈。
- `d50a5fc`：按 review 收敛结果编码与所有权谓词。

### 保留边界

- 结果 24 小时保留、过期读取和幂等清理由 Ticket 06 交付。
- 失租接管、自动执行尝试上限和 analytics 基础设施故障恢复仍由 Ticket 05 交付。
