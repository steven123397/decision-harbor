# 首轮受治理查询链路计划（已完成）

阶段成果与后续状态见 [../status/project_status.md](../status/project_status.md)；按计划 README 约定，本文件在下一阶段计划建立时移除。

## 未规划区

不做：历史列表、鉴权、图表、清理任务、契约新版本自动升级、生产部署。

## 垂直任务切片

1. **策略模块（纯函数）**：先写失败单元测试，再实现 `evaluate`。完成条件：单元测试全绿，覆盖允许/拒绝/边界三组。
2. **API 主体**：config、db、runs、execute、routes、seed、main。完成条件：`POST /api/v1/query-runs` 与 `GET /{id}`、`/health`、`/ready` 行为符合 design/api.md。
3. **数据库与部署层**：Alembic 迁移（含授权）、initdb 镜像（四角色、双库、search_path）、api/web Dockerfile、compose.yaml、.env.example、Makefile。完成条件：`make up` 幂等收敛，`/ready` 200。
4. **Web 工作台**：React 页面、nginx 反代、Vitest 映射单测、Playwright 主流程/拒绝流程。完成条件：浏览器用例通过。
5. **集成测试**：身份分离、seed 幂等、端到端链路。完成条件：容器内 pytest 通过。

## 依赖与顺序

1 → 2 → 3 → (4、5 可并行，但都依赖 3 的栈)。数据集校验随时可跑。

## 验证

- 机械：`git diff --check`、数据集 `python3 validate.py`、`make up` 健康收敛。
- 行为：策略单元测试、双库集成测试、Vitest、Playwright、`/health` `/ready` 与允许/拒绝查询 curl 证据。
- 共识：无（设计已确认）。
