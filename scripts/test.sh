#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

compose=(docker compose)
if [[ -f .env ]]; then
  compose+=(--env-file .env)
fi

"${compose[@]}" up --build -d postgres api web
"${compose[@]}" --profile test run --rm --build api-test
"${compose[@]}" --profile test run --rm --build web-test
"${compose[@]}" --profile test run --rm --build e2e
