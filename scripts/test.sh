#!/usr/bin/env bash
# Unified test entry: dataset validate, unit tests, integration tests, Playwright.
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
export WEB_BASE_URL="${WEB_BASE_URL:-http://127.0.0.1:${WEB_HOST_PORT}}"

echo "== dataset validate =="
python3 datasets/sales-analytics-v1/validate.py

echo "== ensure stack up =="
./scripts/up.sh

echo "== API unit tests (in container) =="
docker compose exec -T api pytest tests/unit -q

echo "== API integration tests (in container) =="
docker compose exec -T \
  -e PLATFORM_DATABASE_URL=postgresql+psycopg://platform_app:platform_app@db:5432/platform \
  -e ANALYTICS_READONLY_URL=postgresql+psycopg://analytics_readonly:analytics_readonly@db:5432/analytics \
  -e ANALYTICS_MIGRATOR_URL=postgresql+psycopg://analytics_migrator:analytics_migrator@db:5432/analytics \
  api pytest tests/integration -q

echo "== Playwright e2e =="
if [[ ! -d apps/web/node_modules ]]; then
  (cd apps/web && npm install)
fi
(cd apps/web && npx playwright install --with-deps chromium)
(cd apps/web && WEB_BASE_URL="${WEB_BASE_URL}" npm run test:e2e)

echo "All tests passed."
