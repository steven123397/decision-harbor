#!/usr/bin/env bash
# DecisionHarbor 统一本地命令：up / test / down（见 docs/design/local-runtime.md）。
# 干净 WSL 环境只依赖 Git、Docker、Docker Compose；一切构建与测试在容器内完成。
set -euo pipefail
cd "$(dirname "$0")"

ensure_env() {
    if [[ ! -f .env ]]; then
        local workspace
        workspace="$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-' | sed 's/^-*//; s/-*$//')"
        sed "s/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=decisionharbor-${workspace:-local}/" \
            .env.example > .env
        echo "已从 .env.example 生成 .env（项目名 decisionharbor-${workspace:-local}）"
    fi
    set -a
    # shellcheck disable=SC1091
    source ./.env
    set +a
}

analytics_owner_url() {
    echo "postgresql+psycopg://analytics_owner:${ANALYTICS_OWNER_PASSWORD}@db:5432/analytics"
}

cmd_up() {
    docker compose build db api web
    docker compose up -d db api web
    echo "==> platform 迁移"
    docker compose run --rm --no-deps api alembic -c alembic.ini -n platform upgrade head
    echo "==> analytics 迁移"
    docker compose run --rm --no-deps -e ANALYTICS_MIGRATION_URL="$(analytics_owner_url)" \
        api alembic -c alembic.ini -n analytics upgrade head
    echo "==> seed 固定数据集"
    docker compose run --rm --no-deps -e ANALYTICS_MIGRATION_URL="$(analytics_owner_url)" \
        api python -m app.seed
    echo "==> 等待 /ready"
    local url="http://localhost:${API_PORT:-8000}/ready"
    for _ in $(seq 1 60); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo "就绪：web http://localhost:${WEB_PORT:-5173} · api http://localhost:${API_PORT:-8000}"
            return 0
        fi
        sleep 1
    done
    echo "错误：等待 $url 超时，请查看 docker compose logs api db" >&2
    return 1
}

cmd_test() {
    cmd_up
    echo "==> 策略与单元测试（pytest）"
    docker compose run --rm --no-deps api pytest -m "not integration" -q
    echo "==> 集成测试（pytest，双库）"
    docker compose run --rm api pytest -m integration -q
    echo "==> 前端组件测试（Vitest）"
    docker compose run --rm --no-deps web npx vitest run
    echo "==> 浏览器主流程（Playwright）"
    docker compose --profile test build e2e
    docker compose --profile test run --rm e2e
    echo "全部测试通过"
}

cmd_down() {
    docker compose --profile test down "$@"
}

usage() {
    cat <<'EOF'
用法：./dev.sh <命令>
  up            构建并启动 web/api/db，执行迁移与 seed，等待就绪（可重复执行）
  test          先确保就绪，再运行单元、集成、组件与浏览器测试
  down [参数]   停止本实例；追加 --volumes 一并删除数据卷
EOF
}

case "${1:-}" in
    up)   ensure_env; cmd_up ;;
    test) ensure_env; cmd_test ;;
    down) ensure_env; shift; cmd_down "$@" ;;
    *)    usage; exit 1 ;;
esac
