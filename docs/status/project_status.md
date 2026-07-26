# 项目状态

更新于 2026-07-26。

## 当前结论

- v0.1 首轮实现已完成（计划见 [v0.1-foundation.md](../plan/v0.1-foundation.md)）：`web/` 查询工作台、`api/` 受治理查询服务、PostgreSQL 双库三身份 Compose 环境、两套 Alembic 迁移、幂等 seed、`./dev.sh up|test|down` 统一命令。
- 验证通过：策略单元测试 47 例、双库集成测试 19 例、Vitest 组件测试 5 例、Playwright 浏览器主链 3 例；`./dev.sh up` 重复执行幂等（seed 跳过）；`/health`、`/ready` 与允许/拒绝查询的 API 实证符合设计。
- 依赖源可配置：`.env` 可设 `PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY`，直连官方源慢的网络用国内镜像。

## 风险

- SQLGlot 升级可能改变解析行为，以策略单元测试用例库为回归防线（见 [query-governance.md](../design/query-governance.md)）。
- Playwright 基础镜像约 743 MB，首次拉取在慢速网络下耗时明显；仅影响首次 `./dev.sh test`。

## 下一步

1. 按需进入下一阶段规划（工作台增强、鉴权等延后决策项均待产品输入）。
