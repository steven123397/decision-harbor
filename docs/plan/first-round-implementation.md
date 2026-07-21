# 首轮实现计划

## 阶段目标

在干净 WSL 环境中一条 `docker compose up` 启动全栈，通过全部测试。

## 任务切片

| # | 任务 | 依赖 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 项目骨架：目录结构、pyproject.toml、package.json、Dockerfile、docker-compose.yml、.env.example、DB 初始化脚本 | 无 | compose config 通过 | 待开始 |
| 2 | SQL 策略引擎 + 单元测试 | 1 | pytest 通过 | 待开始 |
| 3 | API 核心：models、Alembic 迁移、seed、executor、router、main | 1, 2 | pytest 单元通过 | 待开始 |
| 4 | Web 查询工作台 + Vitest | 1 | vitest 通过 | 待开始 |
| 5 | Compose 集成：启动、/health、/ready、允许与拒绝查询 | 1-4 | curl 验证 | 待开始 |
| 6 | 集成测试 + Playwright 浏览器测试 | 5 | pytest + playwright 通过 | 待开始 |

## 未规划区

- 生产部署、CI/CD、监控。
- 查询历史列表 UI。

## 验证标准

- 机械验证：`git diff --check`、`python3 datasets/sales-analytics-v1/validate.py`、pytest、vitest、playwright。
- 行为验证：docker compose up 后 /health 200、/ready 200、允许查询返回结果、拒绝查询返回错误码。
- 共识验证：无（设计已收敛）。
