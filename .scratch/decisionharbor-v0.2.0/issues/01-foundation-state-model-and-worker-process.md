# 01 — 异步运行地基：状态模型、Worker 进程与凭据边界

**What to build:** 让 platform 数据模型能够承载完整的异步查询生命周期，并让执行用户 SQL 的能力只属于独立 Worker 进程。查询运行获得本版本需要的全部状态、取消意图、执行尝试计数、重试关联和幂等记录；Worker 作为一个独立进程在 Compose 环境中启动，加载并校验自己的租约、心跳、轮询和容量配置；API 只保留 platform 凭据与最小就绪探测凭据，不再持有执行用户 SQL 所需的 analytics 查询凭据。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] platform 数据模型支持 `received`、`rejected`、`queued`、`running`、`succeeded`、`failed`、`cancelling`、`cancelled` 全部状态，且 `rejected`、`succeeded`、`failed`、`cancelled` 为不可逆终态
- [ ] platform 数据模型记录每次执行尝试的 Worker 标识、递增 generation、租约时间、心跳时间和尝试序号
- [ ] platform 数据模型记录查询运行的取消意图、执行尝试计数、重试来源关联以及提交与重试的幂等键作用域
- [ ] 非法状态与事实组合被数据库约束拒绝，不能只靠应用代码约定
- [ ] API 运行环境中不存在执行用户 SQL 所需的 analytics 查询凭据，API 只持有 platform 凭据与独立的最小就绪探测凭据
- [ ] 移除 analytics 执行凭据后，API 的 `/ready` 仍能在 1 秒截止时间内成功，`/health` 仍只表达进程存活
- [ ] Worker 作为独立进程在 Compose 中启动，使用默认配置时保持就绪并报告自身健康
- [ ] Worker 在任一配置项非法时以非零退出码启动失败并给出可读原因，包括非正整数、以及心跳不严格小于租约
- [ ] Worker 默认配置为并发上限 4、租约 15000ms、心跳 3000ms、轮询 250ms、最大执行尝试 3 次
- [ ] 既有 SQL AST 默认拒绝、允许对象范围、analytics 只读权限、固定数据口径与超时行数限制回归全部保持通过
