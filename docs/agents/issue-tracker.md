# Issue tracker: GitHub

本仓库的 issue 与规格记录在 GitHub Issues（`steven123397/decision-harbor`，公开仓库）。所有操作使用 `gh` CLI。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`，多行正文用 heredoc。
- **读取 issue**：`gh issue view <number> --comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label`、`--state` 过滤。
- **修改 issue（标题 / 正文）**：`gh issue edit <number> --title "..." --body-file <file>`；改正文先拉当前 `--json body` 在原文上改，不整篇重写丢历史段落。
- **评论**：`gh issue comment <number> --body "..."`
- **加 / 去标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`
- **评论是通知，不是修订**：范围收窄、结论更新等事实变更必须用 `edit` 落到标题与正文；评论区可留决策过程，issue 的事实状态以正文为准。

仓库从 `git remote -v` 推断；`gh` 在 clone 内自动生效。

## 发布前自审查与发布后校验（必做）

批量创建 ticket 前，**必须**把依赖图交给**子代理**审查（替代人工评审，不必等用户确认；不得由主对话自查——拆解作者自审仍是同一双眼睛，漏边正源于此，子代理才有新鲜视角）：

- 子代理拿到自包含输入：规格 issue 正文 + 全部工单的标题与 Blocked by 清单；要求它**独立重推**依赖图（正向：每张工单的 Blocked by 只列真正闸门它的工单；反向：逐张问「它的交付物被谁消费」，每个消费者都必须阻塞于它——漏边多发生在这一侧，典型：终验工单验收 Web 主流程，却没被 Web 工单阻塞；收口 / 终验类工单必须阻塞于全部交付物工单），只报差异与理由，不改数据。
- 主对话按子代理报告修正依赖图后再发布；报告与修正不一致时以重推结论为准并记录分歧。

发布后**必须**运行 `scripts/check-tracker.sh` 校验结构一致性：正文 `## Blocked by` 声明与原生 blocked-by 边**双向一致**（声明的每条边都要有原生边，原生边的每条也要在正文声明），`## Parent` 声明的父 issue 必须真实挂载 sub-issue。退出码非 0 即有缺失，按输出补边（注意 `dependencies/blocked_by` 端点要的是数据库 id，不是 issue 编号）。写正文总会成功，建边调用可能静默失败；自审查管依赖图的语义完备，脚本管正文与原生边的结构一致，缺一不可。

## Pull request 作为分流入口

**PR 作为请求入口：否。**（若今后要把外部 PR 当作功能请求进入分流队列，把此项改为 `yes`，`/triage` 会读取该标志。）

## 技能说「发布到 issue tracker」时

创建一个 GitHub issue。

## 技能说「取相关工单」时

运行 `gh issue view <number> --comments`。

## Wayfinder 操作

供 `/wayfinder` 使用。**地图（map）**是单个 issue，子工单挂在它下面。

- **地图**：单个 issue 打 `wayfinder:map` 标签，正文承载 Notes / Decisions-so-far / Fog。`gh issue create --label wayfinder:map`。
- **子工单**：以 GitHub sub-issue 关联到地图（对 sub-issues 端点调 `gh api`）；未启用时把子项加进地图正文任务清单，并在子工单正文顶部写 `Part of #<map>`。标签用 `wayfinder:<type>`（`research` / `prototype` / `grilling` / `task`）。认领后指派给当前开发者。
- **阻塞关系**：用 GitHub 原生 issue dependencies（UI 可见的权威表示）。加边：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞方的数据库 id（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`），不是 `#number` 也不是 `node_id`。查询用 `issue_dependencies_summary.blocked_by`（只计未关闭阻塞，是生效闸门）。不可用时退回在子工单正文顶部写 `Blocked by: #<n>, #<n>` 行。全部阻塞关闭即视为解除阻塞。
- **前沿查询**：列出地图的未关闭子工单，剔除仍有未关闭阻塞或已有指派人的，按地图顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`——会话的第一次写入。
- **解决**：`gh issue comment <n> --body "<答案>"`，然后 `gh issue close <n>`，再把上下文指针（gist + 链接）追加到地图的 Decisions-so-far。
