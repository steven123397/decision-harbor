# Domain docs

工程 skill 探索 DecisionHarbor 时，先读取根 `CONTEXT.md`，再读取与当前工作相关的 `docs/adr/`。

仓库采用 single-context 布局：

```text
/
├── CONTEXT.md
└── docs/
    ├── adr/
    ├── agents/
    ├── background/
    └── archive/
```

输出涉及领域概念时，使用 `CONTEXT.md` 的规范术语。发现术语冲突时先指出并通过 domain modeling 收敛；不要在 spec、ticket 或代码中静默引入同义词。

`CONTEXT.md` 只保存术语定义和概念边界。只有决策同时满足「难以逆转」「缺少背景会令人意外」「存在真实取舍」时，才写入 `docs/adr/`。实现细节、功能规格、进度和聊天记录分别留在代码、`.scratch/` 和 Git 历史中。
