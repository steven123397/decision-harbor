# 技术约束

## 固定技术栈

首轮实现使用以下技术栈：

| 领域 | 技术 |
| --- | --- |
| Web | Node.js 24、React 19、TypeScript、Vite |
| API | Python 3.13、FastAPI、Pydantic |
| 数据库 | PostgreSQL 18、SQLAlchemy 2.0、Alembic、psycopg 3 |
| SQL 策略 | SQLGlot |
| 本地运行 | Docker Compose |
| 验证 | pytest、Vitest、Playwright |

主要组件的具体版本和用途应在应用建立后记录在项目自身文档与依赖清单中。

## 架构边界

- Web、API 与 PostgreSQL 必须在本地 Docker Compose 环境中协同运行。
- PostgreSQL 在单一容器中提供逻辑隔离的 `platform` 与 `analytics` 数据库。
- 查询策略必须在 API 中基于 SQL AST 与对象访问范围实施；数据库只读身份负责提供独立的权限约束。
- 迁移与 seed 流程必须将固定数据集建立到 `analytics`，平台审计结构由应用自行设计并建立到 `platform`。
- 前端负责最小查询工作台，不承担完整运营功能。

## 可配置性与隔离

本地运行配置必须支持多个独立工作区并行存在：

- Compose 项目名可配置；
- Web 与 API 的宿主端口可配置；
- 不能固定 `container_name`、Docker 网络名、数据卷名或绑定目录；
- 不依赖数据库固定宿主端口；
- 每个实例拥有独立的容器、网络和数据卷命名空间。

## 质量基线

实现必须具备以下验证层次：

1. SQL 策略单元测试，覆盖允许、拒绝、对象范围与边界语法。
2. 双数据库集成测试，证明平台写入身份与分析只读身份的职责分离。
3. 浏览器主流程测试，覆盖输入 SQL、提交、状态展示和结果或拒绝信息。

测试数量和覆盖率不是目标；验证应证明受治理查询链路与项目基座可以可靠运行。
