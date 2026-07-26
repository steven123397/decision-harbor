# 项目状态

更新于 2026-07-26。

## 当前结论

- v0.1 首轮实现已完成（计划见 [v0.1-foundation.md](../plan/v0.1-foundation.md)）：`web/` 查询工作台、`api/` 受治理查询服务、PostgreSQL 双库三身份 Compose 环境、两套 Alembic 迁移、幂等 seed、`./dev.sh up|test|down` 统一命令。
- 两条读取系统目录的绕过路径已修复：把系统 SQL 藏进函数字符串参数（`query_to_xml`），以及用内层同名 CTE 遮蔽物理表后访问 `pg_catalog.pg_class`。根因是策略缺少函数级判定，且用全树 CTE 名集合跳过物理表核对；现收敛为安全函数允许集，并按真实词法作用域判定 CTE 引用，规则与决策见 [query-governance.md](../design/query-governance.md)。两条路径分别以 `policy_forbidden_function`、`policy_forbidden_object` 拒绝，审计记录无行数与耗时，即未进入执行。
- `/ready` 在数据库完全不可达时于连接超时上界内返回未就绪原因，不再挂起（见 [local-runtime.md](../design/local-runtime.md)）。
- 依赖按锁文件安装：`api/requirements.lock` 与两份 `package-lock.json`，Node 侧用 `npm ci`。依赖源仍可经 `.env` 的 `PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY` 切换，且不影响解析结果。
- 验证通过：策略单元测试 102 例、双库集成测试 24 例、Vitest 组件测试 5 例、Playwright 浏览器主链 3 例；`./dev.sh up` 重复执行幂等（seed 跳过）；`/health`、`/ready` 与允许/拒绝查询的 API 实证符合设计。

## 风险

- 函数允许集是默认拒绝的清单，合法但尚未收录的分析函数会被拒绝；扩容按 [query-governance.md](../design/query-governance.md) 的收录标准逐个评估，业务查询语料用例是误伤防线。
- SQLGlot 升级可能改变解析行为与函数规范名，以策略单元测试用例库为回归防线（见 [query-governance.md](../design/query-governance.md)）。
- Playwright 基础镜像约 743 MB，首次拉取在慢速网络下耗时明显；仅影响首次 `./dev.sh test`。

## 下一步

1. 按需进入下一阶段规划（工作台增强、鉴权等延后决策项均待产品输入）。
