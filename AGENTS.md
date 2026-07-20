# DecisionHarbor Agent 工作规则

## 适用范围

本文件是仓库根级长期协作规则。子目录若存在更近的 `AGENTS.md`，以更近规则优先；二者冲突时，更近规则覆盖冲突点，其余仍遵循本文件。

## 项目边界

DecisionHarbor 是一个面向企业内部业务人员的受治理数据分析平台。首轮聚焦显式 SQL 的受控执行链路；自然语言转 SQL、LLM、RAG、MCP、A2A、复杂 RBAC、运营后台与图表编辑器不属于当前范围。

## 默认阅读顺序

新 Agent 进入项目时，按以下顺序恢复上下文（跳过尚不存在的路径）：

1. `README.md` — 产品一句话定位与仓库入口
2. `docs/index.md` — 正式文档导航
3. `docs/status/project_status.md` — 当前事实、风险与下一步
4. `docs/plan/` 下当前有效计划（若有）
5. `docs/design/` 下相关设计（若有）
6. `docs/background/product-requirements.md`
7. `docs/background/technical-constraints.md`
8. `datasets/sales-analytics-v1/README.md`
9. `datasets/sales-analytics-v1/contract.json`

开始实现前，必须阅读第 6–9 项。数据契约、公开 CSV、生成器、校验器和 manifest 是产品输入的一部分。实现迁移与 seed 流程时不得改名、删除或重新解释其中的字段和业务口径。

## 文档目录职责

| 路径 | 职责 | 不写什么 |
| --- | --- | --- |
| `docs/background/` | 外部输入、已确认的产品背景、需求与技术约束 | 实现细节、当前进度、可执行任务切片 |
| `docs/design/` | 长期边界、领域模型、接口、数据与关键决策 | 临时计划、进度日志、背景原文复述 |
| `docs/plan/` | 阶段目标、任务切片、依赖、验证与交付边界 | 最终设计正文、实时状态 |
| `docs/status/` | 当前事实、进展、风险与下一步 | 长期设计、完整计划正文 |
| `docs/index.md` | 导航与阅读入口 | 正文内容的复制粘贴 |

详细约定见 `docs/AGENTS.md`。实现过程产生的产品内生文档写入 `design` / `plan` / `status`，不要改写既有背景约束来迁就实现。

事实来源优先级（冲突时）：

1. 已提交的代码与可运行验证结果
2. `docs/design/` 与 `datasets/**/contract.json`
3. `docs/plan/` 当前有效计划
4. `docs/status/` 当前状态
5. `docs/background/` 背景需求与约束
6. `README.md` 与本文件中的稳定规则

## 长期模块与安全边界

- 用户 SQL 只能在 `analytics` 库上以独立只读身份执行；不得使用平台写入身份执行用户 SQL。
- 平台状态（含查询审计）仅存在于 `platform` 库，由平台可写身份访问。
- SQL 治理基于 AST 与对象访问范围，不能只依赖字符串黑名单。
- 固定分析数据契约与公开 fixture 属于产品输入，变更须同步文档、校验器与测试。
- 本地多工作区并行：Compose 项目名与 Web/API 宿主端口可配置；禁止固定 `container_name`、全局网络名、全局数据卷名或共享绑定目录。

## Git 与工作区

- `.worktrees/` 仅用于本地独立工作区，必须保持未跟踪（已在 `.gitignore` 中）。
- 不要提交密钥、`.env`（保留 `.env.example`）、依赖目录、构建产物与缓存。
- 未经用户明确要求，不要 `commit`、`push`、创建 PR 或删除远程分支。
- 变更跨越 API、数据库、查询策略或运行环境时，同步更新受影响的产品文档与测试。

## 验证与构建产物

统一入口：

```bash
./scripts/up.sh      # 构建并启动 Web / API / PostgreSQL，等待 /ready
./scripts/test.sh    # 数据集校验 + 单元 + 集成 + Playwright
./scripts/down.sh    # 停止当前 Compose 项目
```

数据集单独校验：

```bash
python3 datasets/sales-analytics-v1/validate.py
```

分层验证至少覆盖 SQL 策略单元测试、双数据库集成测试与查询工作台浏览器主流程。

不要提交或依赖以下产物作为事实来源：

- `node_modules/`、`dist/`、`.venv/`、`__pycache__/`、`.pytest_cache/`
- `playwright-report/`、`test-results/`
- 本地 `.env` 与密钥文件

## 禁止写入本文件的内容

不要把当前阶段进度、临时计划、待办清单或会频繁变化的版本事实写入本文件；那些内容属于 `docs/status/` 与 `docs/plan/`。
