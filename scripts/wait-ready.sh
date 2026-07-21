#!/bin/bash
set -e
API_PORT="${DH_API_PORT:-8000}"
python3 - "$API_PORT" <<'PY'
import sys, time, urllib.request
port = sys.argv[1]
for _ in range(60):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/ready", timeout=2) as r:
            if r.status == 200:
                print("ready")
                sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
print("not ready", file=sys.stderr)
sys.exit(1)
PY
