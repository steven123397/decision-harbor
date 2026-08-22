# DecisionHarbor Agent 工作规则

## 1. 任务入口

DecisionHarbor 是面向企业内部业务人员的受治理数据分析平台。根 `AGENTS.md` 是唯一仓库规则入口；规范术语、长期决策和工作票据分别由 `CONTEXT.md`、`docs/adr/` 和本地 Markdown tracker 承载。

开始修改前：

1. 运行 `git status --short --branch`，保留用户已有改动，并只在用户指定的工作树内操作。
2. 读取根 `CONTEXT.md`，再读取与任务相关的 `docs/adr/`。
3. 读取 `.scratch/<feature>/spec.md` 和当前 ticket；创建、认领、阻塞或解决本地票据时，遵循 `docs/agents/issue-tracker.md`。
4. 涉及产品范围、技术栈或固定数据时，读取 `docs/background/` 与 `datasets/sales-analytics-v1/{README.md,contract.json}`。

完成标准是：唯一事实源已更新，ticket 验收条件有可复现证据，本次验证没有留下应提交的生成物。

## 2. Agent skills

### Issue tracker

规格、Wayfinder map 和 tickets 使用仓库内受 Git 跟踪的本地 Markdown tracker。见 `docs/agents/issue-tracker.md`。

### Ticket 状态

本地票据使用 Matt skill 的 5 个默认分流状态，并在实现期间沿同一 `Status` 字段推进生命周期。见 `docs/agents/triage-labels.md`。

### Domain docs

仓库采用 single-context 布局：根 `CONTEXT.md` 维护规范术语，`docs/adr/` 维护长期决策。见 `docs/agents/domain.md`。

## 3. Matt skill 工作流

- 路线仍有大范围未知时使用 `/wayfinder`；路线清晰后使用 `/to-spec`、`/to-tickets`，再逐 ticket 使用 `/implement`。
- `/implement` 内部采用 TDD；交付前使用 `/code-review` 做 Standards 与 Spec 双轴复核。
- `/to-tickets` 生成 tracer-bullet tickets，每张票必须能在一个新上下文中完成，并显式记录 blocking edges。
- 领域术语只由 `CONTEXT.md` 规范；只有满足 ADR 创建条件的长期取舍才写入 `docs/adr/`。
- 新建或修改 agent-facing 文档时使用渐进披露：入口文件只保留步骤、触发条件和指向按需资料的上下文指针。

## 4. 文档事实源

- `CONTEXT.md`：规范术语和概念边界；不保存实现规格、进度或聊天记录。
- `docs/adr/`：难以逆转、缺少背景会令人意外且经过真实取舍的长期决策。
- `.scratch/<feature>/spec.md`：当前功能规格；`.scratch/<feature>/issues/`：实现 tickets、依赖、验收和解决记录。
- `docs/background/`：已确认的外部输入与技术约束，不为迁就实现而改写。
- `docs/archive/`：历史设计、计划和状态，只作按需参考，不是当前事实源。
- `datasets/sales-analytics-v1/contract.json` 与已提交 CSV：固定数据结构、行数和业务口径的权威来源。

## 5. 产品与模块边界

- Web 负责查询工作台和用户可见状态，不判断 SQL 策略或直接访问数据库。
- API 负责 HTTP 合同、同步策略校验和平台侧编排，不使用平台身份执行用户 SQL。
- Worker 负责异步领取和执行已放行查询；用户 SQL 只使用 analytics 只读身份。
- PostgreSQL 分别承载 platform 产品状态与 analytics 固定分析数据；应用策略与数据库权限共同构成治理边界。
- LLM、自然语言转 SQL、RAG、MCP、A2A、复杂 RBAC、运营后台和图表编辑器不属于当前范围。

跨越 API、数据库、SQL 策略、Worker 或运行环境的实现必须读取相关 spec/ADR，并同步更新受影响的决策和测试。

## 6. 验证与 Git

- 固定数据校验：在 `datasets/sales-analytics-v1/` 运行 `python3 validate.py`。
- 应用回归：运行 `./dev test`；需要验证运行环境时使用可配置 Compose 项目名和宿主端口。
- 文档与配置：运行 `git diff --check`，并检查所有新增链接和本地 tracker 引用。
- 缺少依赖或环境时记录具体阻塞，不把未执行检查描述为通过。
- 未经用户明确要求，不提交或推送。提交前核对 diff 范围，不提交环境文件、依赖、缓存、构建产物、测试报告或 `.codegraph/`。
