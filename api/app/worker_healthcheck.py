"""worker 容器健康检查：platform 库心跳标记在 3 个心跳周期内即健康。

独立小脚本（非 app.worker 的一部分）：容器内以
`python -m app.worker_healthcheck <worker_id> <max_age_seconds>` 调用，
exit 0 = 健康。心跳既证明进程活着，也证明它还能到达 platform 库。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text

from app.config import get_settings
from app.db import create_platform_engine


def main() -> int:
    worker_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WORKER_ID", "")
    max_age = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    if not worker_id:
        return 1
    engine = create_platform_engine(get_settings().platform_app_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM worker_hearts "
                    "WHERE worker_id = :w AND beat_at > now() - make_interval(secs => :age)"
                ),
                {"w": worker_id, "age": max_age},
            ).fetchone()
            return 0 if row is not None else 1
    except Exception:
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
