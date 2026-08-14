#!/usr/bin/env bash
set -euo pipefail
if [ -f .env ]; then
  API_PORT="$(sed -n 's/^API_PORT=//p' .env | tail -1)"
fi
API_PORT="${API_PORT:-8000}"
BASE="http://localhost:${API_PORT}"
for _ in $(seq 1 60); do
  if curl -fsS "${BASE}/ready" >/dev/null 2>&1; then
    echo "ready"
    exit 0
  fi
  sleep 2
done
echo "API not ready after 120s" >&2
exit 1
