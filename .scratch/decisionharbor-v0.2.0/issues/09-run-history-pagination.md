# 09 — 运行历史分页

**What to build:** 分析用户查看按创建时间倒序分页的运行历史：`GET /api/v1/query-runs` 返回分页列表，`limit` 默认 20、范围 1 到 100，`cursor` 是服务端生成的不透明字符串。历史按 `(created_at DESC, id DESC)` 稳定排序，相同创建时间以 ID 降序打破平局；翻页期间新插入运行不会使后续页面重复或跳过已取得游标时的记录快照。非法 `limit` 或 `cursor` 返回 422 `invalid_pagination`。

**Blocked by:** 01 — 异步提交与单 Worker 执行的最小闭环

**Status:** ready-for-agent

- [ ] 返回 HTTP 200 与分页历史，沿用统一 `{data, error}` envelope
- [ ] `limit` 默认 20、范围 1 到 100；越界或非法返回 422 `invalid_pagination`
- [ ] `cursor` 为服务端生成的不透明字符串；非法或无法解析的 cursor 返回 422 `invalid_pagination`
- [ ] 按 `(created_at DESC, id DESC)` 稳定排序；相同创建时间以 ID 降序打破平局
- [ ] 下一页严格位于游标记录之后；取得游标后新插入的运行不会使后续页面重复或跳过原快照中的记录
- [ ] 集成测试在真实数据库上构造相同 `created_at` 平局与并发插入场景
