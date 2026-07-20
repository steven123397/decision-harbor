#!/usr/bin/env bash
# 统一测试命令：策略单元测试、双库集成测试、前端单元测试、浏览器主流程。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env"
fi

echo "== 1/4 SQL 策略单元测试 =="
docker compose run --rm --no-deps --entrypoint python api -m pytest tests/unit -q

echo "== 2/4 前端单元测试 (Vitest) =="
docker compose --profile test run --rm web-unit

echo "== 3/4 双库集成测试 =="
docker compose up --build --wait --detach db migrate api web
# 经 migrate 服务运行（同镜像且挂载 /datasets，seed 幂等用例需要）
docker compose run --rm --no-deps --entrypoint python migrate -m pytest tests/integration -q

echo "== 4/4 浏览器主流程 (Playwright) =="
docker compose --profile test build e2e
docker compose --profile test run --rm e2e

echo "全部测试通过。"
