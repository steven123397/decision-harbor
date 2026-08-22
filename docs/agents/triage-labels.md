# Ticket 状态

本地 Markdown tracker 使用单一 `Status` 字段。票据先经过 Matt skill 的 5 个默认分流状态，进入实现后沿同一字段推进，不另设并行的 triage 字段。

| Matt skill 状态 | 本地状态 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格完整，可交给 Agent |
| `ready-for-human` | `ready-for-human` | 需要人工参与 |
| `wontfix` | `wontfix` | 明确不处理 |

技能提到规范分流角色时，使用右列字符串。实现票据的正常转换为：

```text
needs-triage | needs-info | ready-for-human
                    ↓
              ready-for-agent → claimed → resolved
                    ↑              ↓
                    └── blocked ────┘
```

- `claimed`：当前实现上下文已经认领。
- `blocked`：实现无法继续，`## Comments` 已记录具体阻塞；解除后回到 `ready-for-agent`。
- `resolved`：验收条件、验证证据和 review 均已记录。
- `wontfix` 与 `resolved` 都不再进入 frontier；`wontfix` 表示明确放弃，不能代替已交付结论。
