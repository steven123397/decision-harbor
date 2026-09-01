# 09 — 运行历史分页

**What to build:** 分析用户查看按创建时间倒序分页的运行历史：`GET /api/v1/query-runs` 返回分页列表，`limit` 默认 20、范围 1 到 100，`cursor` 是服务端生成的不透明字符串。历史按 `(created_at DESC, id DESC)` 稳定排序，相同创建时间以 ID 降序打破平局；翻页期间新插入运行不会使后续页面重复或跳过已取得游标时的记录快照。非法 `limit` 或 `cursor` 返回 422 `invalid_pagination`。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** resolved

- [x] 返回 HTTP 200 与分页历史，沿用统一 `{data, error}` envelope
- [x] `limit` 默认 20、范围 1 到 100；越界或非法返回 422 `invalid_pagination`
- [x] `cursor` 为服务端生成的不透明字符串；非法或无法解析的 cursor 返回 422 `invalid_pagination`
- [x] 按 `(created_at DESC, id DESC)` 稳定排序；相同创建时间以 ID 降序打破平局
- [x] 下一页严格位于游标记录之后；取得游标后新插入的运行不会使后续页面重复或跳过原快照中的记录
- [x] 集成测试在真实数据库上构造相同 `created_at` 平局与并发插入场景

## Resolution

**交付内容：** `GET /api/v1/query-runs` 分页历史端点。

- `pagination.py`（新增）：不透明游标编解码（带版本前缀的 URL 安全 base64；naive datetime、非法 UUID、未知版本与非本服务输入一律解析失败）与 `limit` 边界解析（默认 20、1 到 100、仅 ASCII 十进制）。
- `domain.py`（新增类型）：`HistoryCursor`（键集位置 `(created_at, id)`）与 `HistoryPage`（一页运行 + 下一页游标）。
- `repository.py`：`list_history` 以键集条件 `WHERE (created_at, id) < (:cursor_created_at, :cursor_id)` 配合 `ORDER BY created_at DESC, id DESC` 与 `LIMIT :limit` 实现分页；行向量比较天然保证下一页严格位于游标记录之后，新插入不产生重复或跳过。
- `api.py`：新路由（声明于 `/{run_id}` 之前）；查询参数手动解析，非法参数在触碰审计存储前返回 422 `invalid_pagination`；存储异常映射 503 `audit_unavailable`；提取 `_audit_unavailable_response()` 并复用到既有 get/get result 端点（消除重复）。响应 `data` 为 `{query_runs: [...], next_cursor: string | null}`。
- `migrations/platform/versions/0008_run_history_index.py`：`(created_at DESC, id DESC)` 索引避免分页全表排序；`readiness.py` 的 `PLATFORM_MIGRATION_VERSION` 同步 bump 至 `platform_0008`。

**实际验证：**

- 单元（本机）：`tests/unit/test_pagination.py` 6 项、`tests/unit/test_api.py` 新增 7 项（envelope、limit 边界、非法 limit/cursor 422、游标转发、审计失败 503 脱敏），全套 177 passed。
- 集成（Compose 真实数据库，`pytest tests/integration/test_run_history.py`，10 passed）：默认 20 条稳定顺序；`created_at` 平局以 id DESC 打破；平局横跨页边界保持稳定；limit 1/3/20 全量翻页无重复无遗漏；取得游标后插入更新记录与平局记录、翻页期间 ThreadPoolExecutor 并发提交——续页均不重复、不跳过原快照记录；非法 limit/cursor 真实链路 422；多状态运行入列；游标往返不透明且两页不重叠。
- 全量回归（`API_HOST_PORT=18000 WEB_HOST_PORT=15173 ./dev test`，宿主端口与默认 8000 冲突时按可配置端口运行）：API 271 passed、web 单测 33 passed、e2e 4 passed。
- `git diff --check` 通过。

**Review 结论：** `/code-review` 双轴复核。Standards 轴：无硬违规；两处 Duplicated Code（测试 `platform_engine` 与 `audit_unavailable` 响应块）已修复（复用 conftest 的 engine、提取 `_audit_unavailable_response()`）；`CURSOR_VERSION` 判断为可辩护保留（语义已定义且有测试覆盖）。Spec 轴：合同逐条落实；`audit_unavailable` 503 沿用同资源既有惯例；`len(runs) == limit` 判定在剩余记录恰为 limit 整数倍时会多发一次空页 next_cursor（不违反 spec 文字，客户端多翻一页即收敛），作为已知边界记录在此。

**保留风险：**

- 恰满页时 `next_cursor` 指向空页：客户端多一次空请求，无重复或跳过；如需消除可改 limit+1 探测。
- 迁移引入新索引后旧运行环境需重启 API 触发迁移（readiness 版本已同步，未迁移环境 `/ready` 将按既有 fail-closed 语义返回 503）。
