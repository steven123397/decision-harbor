#!/usr/bin/env bash
# Unified local bring-up: build, start, wait until API is ready.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-decisionharbor}"
export API_HOST_PORT="${API_HOST_PORT:-8000}"
export WEB_HOST_PORT="${WEB_HOST_PORT:-5173}"
export VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${API_HOST_PORT}}"

echo "Compose project: ${COMPOSE_PROJECT_NAME}"
echo "API host port:   ${API_HOST_PORT}"
echo "Web host port:   ${WEB_HOST_PORT}"

docker compose up -d --build

echo "Waiting for API /ready ..."
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${API_HOST_PORT}/ready" >/dev/null 2>&1; then
    echo "API ready"
    curl -fsS "http://127.0.0.1:${API_HOST_PORT}/health"
    echo
    curl -fsS "http://127.0.0.1:${API_HOST_PORT}/ready"
    echo
    echo "Web: http://127.0.0.1:${WEB_HOST_PORT}"
    exit 0
  fi
  sleep 2
done

echo "API did not become ready in time" >&2
docker compose logs --no-color --tail=200 api db || true
exit 1
