# 项目状态

最后更新：2026-08-16。本文件只记录当前事实，过期内容直接改写，不保留历史。

## 当前结论

- 首轮目标已实现并通过全套验证：受治理 SQL 查询链路（AST 策略 → 只读执行 → 审计）、最小查询工作台、双数据库与幂等 seed、可并行 Compose 栈全部可运行。
- 2026-08-16 review 修复轮完成：策略引擎改为函数白名单 + CAST 类型白名单 + 支持节点白名单，表引用按 SQLGlot 词法作用域解析（封堵 CTE 遮蔽/自引用、`::regclass`、`query_to_xml`、`current_user` 等绕过路径）；初始化拆分为一次性 init 容器，API 容器只持有低权限凭据；执行器改用 PostgreSQL 服务端游标（结果集不再在 API 内存物化）；容量耗尽语义修正为 `failed/QY_CAPACITY_EXCEEDED`，连接失败映射 `QY_ANALYTICS_UNAVAILABLE`，不再遗留 `running` 审计；非整数路径 id 正确返回 404；Web 单元测试移入专门测试镜像；datasets/initdb 烤进镜像消除共享绑定目录；契约 `allowed_values`、折扣区间与成本/标价规则落为数据库 CHECK 约束；配置增加上下界校验；E2E 补执行中状态与失败面板。
- 验证基线以本轮实际运行为准（单元/集成/Vitest/Playwright 全绿），数字见「验证」。

## 进展

- `api/`：FastAPI 应用（config/db/policy/runs/execute/routes/seed/readiness）、`bootstrap.py` 仅作为 init 容器入口、Alembic 迁移、容器内测试。
- `web/`：React 19 + Vite 工作台、nginx 反向代理、Vitest 单测、Playwright 用例。
- `deploy/`：compose 栈（db/init/api/web + test profile 的 api-test/web-test）、initdb 脚本烤进 `Dockerfile.db`（四角色、双库、CONNECT 收紧、search_path）。
- 根：`Makefile`（up/down/test/dataset-validate）、`.env.example`、根 `.dockerignore`（仓库根构建上下文）。
- 设计文档六份在 `docs/design/`，已同步本轮决策（init 容器引导、白名单策略、CHECK 约束、测试镜像职责）。

## 验证

```bash
make up          # 构建并启动（init 完成迁移与 seed），等待 health/ready
make test        # 单元 → 集成（api-test）→ Web（web-test）→ 浏览器
make dataset-validate
```

- 单元测试在 api 容器执行（`make unit-test`），集成测试在一次性 `api-test` 容器执行（owner 凭据只进该容器）。
- Web 单元测试在 `web-test` 测试镜像执行（不再进入生产 Nginx 容器）。
- 浏览器测试需要宿主 `web/` 下 `npm install` 与 `npx playwright install chromium`（首次）。
- 幂等：重复 `make up` 与 `restart` 后行数不变（100/8/50/1000/3000）、seed 标记不变、审计记录持久。
- 治理绕过回归：CTE 遮蔽、`::regclass`、`query_to_xml`、`current_user` 在单元与集成两层锁定拒绝。

## 风险与注意

- owner 凭据只注入一次性 init 容器与 api-test 测试容器，仍是本地开发默认值（数据库不发布宿主端口）；生产化需密钥管理。
- 容量限制（连接池与信号量）只在单个 API 进程内生效；多实例部署需重新设计协调，属延后决策。
- seed 标记写入前的进程崩溃会留下「数据已载、标记为空」状态，当前按设计 fail-hard（提示人工清卷）；可考虑行数全等时的精确自愈，属延后决策。
- 本地迭代需重建镜像（源码与数据集构建进镜像、无绑定挂载），属设计取舍。
- Docker 构建缓存曾出现快照损坏（builder 内部状态），`docker builder prune` 后恢复；如再现与代码无关。

## 下一步

1. 提交本轮成果并按需建立里程碑标签。
2. 按延后决策推进：查询运行清理策略、结果导出、seed 新版本升级路径。
3. 视需要引入 Web 开发态热重载 compose 覆盖文件。
