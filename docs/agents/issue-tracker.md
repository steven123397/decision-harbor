# Issue tracker：本地 Markdown

DecisionHarbor 的 Wayfinder map、规格和 tickets 使用仓库内受 Git 跟踪的 `.scratch/`。GitHub Issues 不是此工作流的事实源，不读取或修改其内容来推进主工作树任务。

## 路径约定

- 一个 effort 使用一个目录：`.scratch/<feature-slug>/`。
- Wayfinder map：`.scratch/<effort>/map.md`。
- 功能规格：`.scratch/<feature>/spec.md`。
- 实现 tickets：`.scratch/<feature>/issues/<NN>-<slug>.md`，按依赖顺序从 `01` 编号。
- ticket 对话和过程事实追加到 `## Comments`；解决结论写入 `## Resolution`。

`.scratch/` 是本项目的本地 tracker，不是可丢弃缓存。规格、tickets 和解决记录应进入对应分支的 Git 历史；不得加入 `.gitignore`。

## Wayfinder 操作

- Map 保存 Destination、Notes、Decisions so far、Not yet specified 和 Out of scope，只作为索引，不复制 ticket 的完整结论。
- 决策 ticket 写入 `.scratch/<effort>/issues/NN-<slug>.md`，使用 `Type: research|prototype|grilling|task`、`Status: open|claimed|resolved` 和 `Blocked by: NN, NN`。
- Frontier 是 `open`、未被未解决 ticket 阻塞且未被认领的票；按编号选择第一张。
- 认领时先把状态改为 `claimed`。解决时追加 `## Answer`，把状态改为 `resolved`，再在 map 的 Decisions so far 添加一行名称、链接和结论摘要。
- 路线已经清晰时直接进入 spec，不为流程完整性创建空 map。

## Spec 与实现 tickets

- `/to-spec` 将已确认讨论合成为 `.scratch/<feature>/spec.md`。spec 是用户目标、外部合同和测试边界的事实源。
- `/to-tickets` 在用户确认拆分后，为每张 tracer-bullet ticket 创建独立文件。每张票必须交付可验证的纵向行为，列出真实 blocker，并能在一个新上下文中完成。
- ticket 的单一 `Status` 字段遵循 `docs/agents/triage-labels.md`。通常由 `ready-for-agent` 进入 `claimed`，需要暂停时进入 `blocked`，验收完成后进入 `resolved`；不同时维护另一套 triage 状态。
- `ready-for-agent` 表示规格完整，不表示 `Blocked by` 已解除。`blocked` 必须在 `## Comments` 记录阻塞原因；解除后回到 `ready-for-agent`，由后续上下文重新认领。
- 当前 frontier 是状态为 `ready-for-agent`、所有 `Blocked by` 均为 `resolved`、且尚未认领的 ticket。

## Ticket 闭环

1. 开始前读取完整 spec、ticket、相关 `CONTEXT.md` 术语和 ADR。
2. 将 `Status` 从 `ready-for-agent` 改为 `claimed`，这是本次实现的第一次 tracker 写入。
3. 以 TDD 交付 ticket 的纵向行为，运行最窄可证明验证和必要回归。
4. 使用 `/code-review` 完成 Standards 与 Spec 双轴复核；修复发现后重新复核。
5. 只勾选已有证据的验收条件。在 `## Resolution` 记录结果、实际验证、review 结论、提交 SHA 或未提交事实，以及保留风险。
6. 全部验收满足后把状态改为 `resolved`；报告新进入 frontier 的票，不在同一实现上下文中顺带开始下一张。
