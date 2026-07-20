#!/bin/sh
set -eu

echo "Waiting for postgres..."
i=0
until python - <<'PY'
import os, sys
import psycopg
url = os.environ.get("POSTGRES_ADMIN_URL", "postgresql://postgres:postgres@db:5432/postgres")
try:
    with psycopg.connect(url, connect_timeout=2) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    sys.exit(0)
except Exception as e:
    print(e, file=sys.stderr)
    sys.exit(1)
PY
do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "Postgres not ready" >&2
    exit 1
  fi
  sleep 1
done

python scripts/migrate_and_seed.py
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
