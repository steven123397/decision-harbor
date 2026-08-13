# DecisionHarbor Agent 工作规则

## 适用范围

本文件是仓库根规则。更近的子树 `AGENTS.md` 优先于本文件的同主题条款；本文件未覆盖的主题仍以本文件为准。当前没有独立子树规则。文档目录的写入与读取边界见 `docs/AGENTS.md`。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 默认阅读顺序

开始实现或恢复项目状态时，按以下顺序阅读。不要把当前阶段、最新进度或临时计划写回本文件。

1. 本文件
2. `README.md`
3. `docs/index.md`
4. `docs/status/project_status.md`
5. 与任务相关的 `docs/plan/`、`docs/design/`
6. `docs/background/product-requirements.md`
7. `docs/background/technical-constraints.md`
8. `datasets/sales-analytics-v1/README.md`
9. `datasets/sales-analytics-v1/contract.json`

事实来源：

- 产品背景、需求和已确认约束：`docs/background/`
- 数据契约、字段与业务口径：`datasets/sales-analytics-v1/contract.json`
- 长期设计决策：`docs/design/`
- 阶段计划与任务切片：`docs/plan/`
- 当前进展、风险和下一步：`docs/status/`
- 文档导航：`docs/index.md`，不复制正文

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档目录职责

- `docs/background/`：外部输入、需求背景和技术约束；不要改写既有背景约束来迁就实现。
- `docs/design/`：长期边界、领域模型、接口、数据与关键决策。
- `docs/plan/`：阶段目标、任务切片、依赖、验证和交付边界。
- `docs/status/`：当前事实、进展、风险和下一步。
- `docs/index.md`：只负责导航和阅读入口，不复制正文。

实现过程产生的产品内生文档写入 `design/`、`plan/` 或 `status/`，不要平行复制背景资料。变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## 长期模块与安全边界

- Web、API 与 PostgreSQL 在本地 Docker Compose 中协同运行。
- 单一 PostgreSQL 容器提供逻辑隔离的 `platform` 与 `analytics` 数据库。
- 用户 SQL 只在 `analytics` 上用独立只读身份执行；策略检查基于 SQL AST 与对象访问范围，不能只依赖字符串黑名单。
- 平台审计结构属于 `platform`，不得用平台写入身份执行用户 SQL，也不得跨库访问平台状态。
- 对象范围、拒绝规则和最小 HTTP 接口以 `docs/background/` 为准；表字段与业务口径以数据契约为准。本文件不复述这些清单。

## Git、验证和提交限制

- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪；不得因其未跟踪而清理或删除。
- 不要提交密钥、依赖目录、构建产物、缓存和测试报告；以根 `.gitignore` 为准。
- 改动 `datasets/sales-analytics-v1/` 后，在该目录运行 `python3 validate.py`。
- 启动完整环境：`./scripts/up.sh`。
- 运行项目测试：`./scripts/test.sh`。
- 不要自动 commit、push 或创建 worktree。

## 缓存、依赖与构建产物

以下路径不得纳入版本库，也不应作为事实来源：

- `node_modules/`、`dist/`、`.venv/`、`__pycache__/`、`.pytest_cache/`
- `playwright-report/`、`test-results/`
- `.env` 与 `.env.*`（`.env.example` 除外）
