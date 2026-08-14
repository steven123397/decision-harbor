# DecisionHarbor Agent 工作规则

## 适用范围与优先级

- 本文件适用于整个仓库；更深层目录中的 `AGENTS.md` 可以补充更具体的边界，冲突时以更近的规则为准。
- 任务指定工作树后，只在当前工作树内读写；`.worktrees/` 仅用于本地独立工作区，不纳入项目文档或交付内容。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 默认阅读顺序

开始实现前，先按以下顺序阅读：

1. `README.md`
2. `docs/index.md`
3. `docs/background/product-requirements.md`
4. `docs/background/technical-constraints.md`
5. `datasets/sales-analytics-v1/README.md`
6. `datasets/sales-analytics-v1/contract.json`
7. `docs/status/project_status.md`
8. 按任务需要阅读 `docs/design/` 和 `docs/plan/` 中的正式文档。

进入 `docs/` 子树后，还必须遵守 `docs/AGENTS.md`。

数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档职责

- `docs/index.md` 只负责正式文档的导航和推荐阅读顺序，不复制正文。
- `docs/background/` 保存外部输入、已确认的产品背景、需求和技术约束；实现不得为了方便而改写其业务口径。
- `docs/design/` 保存长期有效的产品内生设计、领域边界、接口、数据不变量和关键决策。
- `docs/plan/` 保存阶段目标、任务切片、依赖、验证和交付边界，不替代设计或状态记录。
- `docs/status/` 保存当前事实、进展、风险和下一步，不把临时状态写回长期规则。
- 同一事实只保留一个权威来源；其他文档使用链接引用，避免平行维护。

## 文档与工作区

- 实现过程产生的产品内生文档应遵循后续建立的项目文档结构；不要改写既有背景约束来迁就实现。
- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## Git 与验证

- 未经用户在当前任务中明确授权，不执行 `git add`、`git commit` 或 `git push`。
- 纯文档变更完成前至少运行 `git diff --check`；涉及固定数据集时，在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py`。
- 应用建立后，新增或修改的运行命令、迁移和测试以仓库实际工具链为准，并在受影响的正式文档中记录。
