# Issue tracker: GitHub

本仓库的 issue 与规格记录在 GitHub Issues（`steven123397/decision-harbor`，公开仓库）。所有操作使用 `gh` CLI。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`，多行正文用 heredoc。
- **读取 issue**：`gh issue view <number> --comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，按需加 `--label`、`--state` 过滤。
- **评论**：`gh issue comment <number> --body "..."`
- **加 / 去标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

仓库从 `git remote -v` 推断；`gh` 在 clone 内自动生效。

## 发布校验（必做）

`/to-tickets`、`/wayfinder` 或任何批量创建 ticket 的操作完成后，**必须**运行 `scripts/check-tracker.sh` 校验结构一致性：正文 `## Blocked by` 段声明的每条依赖都要有原生 blocked-by 边，`## Parent` 声明的父 issue 必须真实挂载 sub-issue。脚本退出码非 0 即有缺失，按输出补边（注意 `dependencies/blocked_by` 端点要的是数据库 id，不是 issue 编号）。正文写了边而原生边缺失是已知风险——写正文总会成功，建边调用可能静默失败。

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
