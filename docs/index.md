# 文档索引

本索引是 `docs/` 的阅读入口，只负责导航。目录职责与写入规则见 [AGENTS.md](AGENTS.md)。

## 按意图查找

- 了解产品定位与治理范围：根 `README.md`、[background/product-requirements.md](background/product-requirements.md)
- 了解技术栈与架构约束：[background/technical-constraints.md](background/technical-constraints.md)
- 了解固定数据契约：`datasets/sales-analytics-v1/README.md` 与 `datasets/sales-analytics-v1/contract.json`（仓库级产品输入，不在 `docs/` 内）
- 了解当前进度、验证命令与下一步：[status/project_status.md](status/project_status.md)
- 了解长期设计决策：design/ 文档，角色与模板见 [design/README.md](design/README.md)
  - [architecture.md](design/architecture.md)：组件、数据流、运行拓扑与并行工作区隔离
  - [query-governance.md](design/query-governance.md)：SQLGlot AST 策略、拒绝码与资源限制
  - [data-and-seeding.md](design/data-and-seeding.md)：双库角色、迁移与幂等 seed
  - [api.md](design/api.md)：HTTP 端点、统一响应与错误语义
  - [workbench.md](design/workbench.md)：最小查询工作台
  - [testing.md](design/testing.md)：单元、集成与浏览器测试接缝
- 了解阶段计划与任务切片：[plan/](plan/)，角色与模板见 [plan/README.md](plan/README.md)
- 了解状态文档的维护规则：[status/README.md](status/README.md)
