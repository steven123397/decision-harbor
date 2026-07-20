#!/usr/bin/env bash
# 统一启动命令：构建、启动全栈、迁移、seed，并等待健康与就绪。
# 可重复执行（迁移 at head 为 no-op，seed 幂等重载）。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env（多工作区并行时请修改其中的项目名与端口）"
fi

docker compose up --build --wait --detach

echo "== /health =="
docker compose exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"

echo "== /ready =="
docker compose exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"

echo "启动完成。工作台: http://localhost:${DH_WEB_PORT:-5173}  API: http://localhost:${DH_API_PORT:-8000}"
