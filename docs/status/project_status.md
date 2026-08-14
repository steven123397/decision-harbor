# 项目状态

最后更新：2026-08-14。本文件只记录当前事实，过期内容直接改写，不保留历史。

## 当前结论

- 首轮目标已实现并通过全套验证：受治理 SQL 查询链路（AST 策略 → 只读执行 → 审计）、最小查询工作台、双数据库与幂等 seed、可并行 Compose 栈全部可运行。
- 验证基线：59 个 API 测试（51 单元 + 8 集成）、4 个 Vitest、2 个 Playwright 浏览器用例全部通过；`make up` 幂等收敛、`/ready` 200。

## 进展

- `api/`：FastAPI 应用（config/db/policy/runs/execute/routes/seed/readiness/bootstrap）、Alembic 迁移、容器内测试。
- `web/`：React 19 + Vite 工作台、nginx 反向代理、Vitest 单测、Playwright 用例。
- `deploy/`：compose 栈（api/web/db）、initdb 脚本（四角色、双库、CONNECT 收紧、search_path）。
- 根：`Makefile`（up/down/test/dataset-validate）、`.env.example`。
- 设计文档六份在 `docs/design/`；本轮实现落地了两个部署细节：PG18 卷需挂载 `/var/lib/postgresql`、platform 库撤销 PUBLIC CONNECT（后者是集成测试发现的边界补强）。

## 验证

```bash
make up          # 构建并启动，等待 health/ready
make test        # 单元 → 集成 → Web → 浏览器
make dataset-validate
```

- 容器内：`pytest tests`（59 passed）。
- Web：`npx vitest run`（4 passed）、`WEB_URL=http://localhost:8080 npx playwright test`（2 passed）。
- 浏览器测试需要宿主 `web/` 下 `npm install` 与 `npx playwright install chromium`（首次）。
- 幂等：重复 `make up` 与 `restart` 后行数不变（100/8/50/1000/3000）、seed 标记不变、审计记录持久。

## 风险与注意

- 引导身份凭据是本地开发默认值，仅 compose 网络内有效（数据库不发布宿主端口）；生产化需密钥管理。
- 本地迭代需重建镜像（源码构建进镜像、无绑定挂载），属设计取舍。
- Docker 构建缓存曾出现快照损坏（builder 内部状态），`docker builder prune` 后恢复；如再现与代码无关。

## 下一步

1. 提交本轮成果并按需建立里程碑标签。
2. 按延后决策推进：查询运行清理策略、结果导出、seed 新版本升级路径。
3. 视需要引入 Web 开发态热重载 compose 覆盖文件。
