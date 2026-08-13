#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

compose=(docker compose)
if [[ -f .env ]]; then
  compose+=(--env-file .env)
fi

"${compose[@]}" up --build -d postgres api web

api_port="${API_HOST_PORT:-8000}"
web_port="${WEB_HOST_PORT:-5173}"
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
  api_port="${API_HOST_PORT:-$api_port}"
  web_port="${WEB_HOST_PORT:-$web_port}"
fi

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${api_port}/ready" >/dev/null && curl -fsS "http://127.0.0.1:${web_port}/" >/dev/null; then
    echo "DecisionHarbor is ready on http://127.0.0.1:${web_port}"
    exit 0
  fi
  sleep 2
done

echo "services did not become ready" >&2
"${compose[@]}" ps
exit 1
