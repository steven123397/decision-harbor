# DecisionHarbor ADR profile

本文件是 `domain-modeling` skill 的项目级轻量扩展，不替代该 skill 的 ADR 创建门槛。只有决策同时满足「难以逆转」「缺少背景会令人意外」「存在真实取舍」时，才创建 ADR。

## 推荐结构

```markdown
---
status: accepted | implemented | partially-implemented | superseded by ADR-NNNN | retired
date: YYYY-MM-DD
---

# 决策标题

## Context

## Decision

## Considered options

## Consequences

## Implementation and evidence

## Revisit when
```

`status`、`date`、`Context` 和 `Decision` 是必填内容。其余章节只在能够增加决策信息时加入，不创建空章节：

- `Considered options` 记录真正竞争过且未来可能再次被提出的方案。
- `Consequences` 记录非显然的代价、约束和后续影响。
- `Implementation and evidence` 只在已有实现时加入，引用本地 ticket、提交和测试，不复制完整日志。
- `Revisit when` 只记录可判断的重新决策条件。

`status` 描述决策的产品落地状态，不替代 `.scratch/` 中 spec 和 ticket 的执行状态。实现改变 ADR 的实际落地状态时，在同一交付中更新 ADR，再进入 `/code-review`。决策被替代或退役时保留原文件，并记录演进关系。
