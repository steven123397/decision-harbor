"""容器入口：等待数据库 → Alembic 迁移（owner）→ 幂等 seed → 启动服务。

迁移与 seed 使用高权限身份，服务进程只持有 platform_app 与
analytics_readonly（见 docs/design/architecture.md 引导决策）。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.db import create_platform_engine
from app.seed.loader import ensure_dataset, load_facts

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("decision_harbor.bootstrap")


def wait_for_postgres(dsn: str, timeout_s: int = 120) -> None:
    import psycopg

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=5):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"等待 PostgreSQL 超时：{dsn.rsplit('@', 1)[-1]}")
            time.sleep(1.5)


def run_migrations() -> None:
    alembic_cfg = Config(Path(__file__).parent.parent / "alembic.ini")
    alembic_cfg.set_main_option(
        "script_location", str(Path(__file__).parent.parent / "migrations")
    )
    settings = get_settings()
    alembic_cfg.set_main_option("sqlalchemy.url", settings.platform_owner_url)
    command.upgrade(alembic_cfg, "head")


def run_seed() -> str:
    settings = get_settings()
    owner_engine = create_platform_engine(settings.platform_owner_url)
    try:
        return ensure_dataset(
            Path(settings.dataset_dir),
            settings.analytics_owner_dsn,
            owner_engine,
        )
    finally:
        owner_engine.dispose()


def main() -> None:
    settings = get_settings()
    logger.info("等待 PostgreSQL 就绪")
    wait_for_postgres(settings.analytics_owner_dsn)

    logger.info("执行 platform 迁移")
    run_migrations()

    logger.info("检查/加载固定数据集")
    outcome = run_seed()
    logger.info("seed 结果：%s（标记 %s）", outcome, load_facts(Path(settings.dataset_dir)).marker_key)

    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    sys.exit(main())
