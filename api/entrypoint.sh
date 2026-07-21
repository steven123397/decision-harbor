#!/bin/bash
set -e
if [ "${DH_SKIP_BOOTSTRAP:-0}" != "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
  echo "[entrypoint] seed analytics"
  python -m app.seed
fi
exec "$@"
