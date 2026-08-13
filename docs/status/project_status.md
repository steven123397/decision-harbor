# 项目状态

## 当前结论

首轮受治理查询链路已实现并可在本工作区 Compose 项目中运行。统一入口是 `./scripts/up.sh` 与 `./scripts/test.sh`。

## 进展

- `api/` 提供 SQLGlot 策略、查询运行、审计、双库迁移与幂等 seed。
- `web/` 提供最小查询工作台，并通过同源路径反代 API。
- Compose 项目名与 Web/API 宿主端口可配置；PostgreSQL 不发布固定宿主端口。
- 已验证：`git diff --check`、数据集校验、API 25 项测试、Vitest 3 项、Playwright 2 项、`/health`、`/ready`、允许与拒绝查询、浏览器主链。

## 风险

- 无应用层登录；访问边界是本地 Compose 网络隔离。
- 卡在 `running` 的查询运行首轮不回收。
- API 镜像首次构建会从 PyPI 拉取依赖，在慢网络下可能耗时较长。

## 下一步

按仓库规则审阅后提交；不要把本工作区的 `.env` 纳入版本库。
