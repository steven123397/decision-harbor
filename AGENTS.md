# DecisionHarbor Agent 工作规则

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 开发前阅读

开始实现前，先阅读：

- `README.md`
- `docs/background/product-requirements.md`
- `docs/background/technical-constraints.md`
- `datasets/sales-analytics-v1/README.md`
- `datasets/sales-analytics-v1/contract.json`

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档与工作区

- `docs/background/` 保存已确认的产品背景、需求和技术约束。
- 实现过程产生的产品内生文档应遵循后续建立的项目文档结构；不要改写既有背景约束来迁就实现。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## 文档结构与阅读顺序

`docs/` 下正式文档按职责分目录，导航入口为 `docs/index.md`，子树规则见 `docs/AGENTS.md`：

- `docs/background/`：外部输入、需求背景与约束来源，确认后不回改。
- `docs/design/`：长期边界、领域模型、接口、数据与关键决策。
- `docs/plan/`：阶段目标、任务切片、依赖、验证与交付边界。
- `docs/status/`：当前事实、进展、风险与下一步。

新 Agent 默认阅读顺序：`README.md` → `docs/index.md` → `docs/background/` → `docs/design/` → `docs/plan/` → `docs/status/`。同一事实只在一个角色目录维护，其他位置引用而不复制。

## 适用范围与验证

- 本文件适用于整个仓库；子目录存在更具体的 `AGENTS.md` 时，以更近者为准。
- 统一运行命令：`scripts/run.sh`（构建、启动、迁移、seed、等待就绪）；统一测试命令：`scripts/test.sh`（策略单元、双库集成、前端单元、浏览器主流程）。两者仅需 Docker 与 Docker Compose。
- 数据集完整性校验：在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py`。
- 依赖、缓存与构建产物保持未跟踪，以 `.gitignore` 为准。
