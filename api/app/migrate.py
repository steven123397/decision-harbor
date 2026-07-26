"""迁移、授权与 seed 的统一入口：python -m app.migrate。

步骤对应 docs/design/data-and-runtime.md 的启动链路：
双库 Alembic 迁移 → analytics 只读授权 → 幂等 seed。
重复执行安全：迁移 at head 为 no-op，授权幂等，seed 截断重载。
"""

from __future__ import annotations

from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import analytics_owner_engine
from app.seed import run as run_seed

# 最小表授权：只授予契约五表；清除旧版的全表授权与默认授权，
# 使未来新增的 analytics 表不会自动对查询身份可见。
_GRANTS = [
    "GRANT USAGE ON SCHEMA analytics TO analytics_readonly",
    "ALTER DEFAULT PRIVILEGES FOR ROLE analytics_owner IN SCHEMA analytics "
    "REVOKE SELECT ON TABLES FROM analytics_readonly",
    "REVOKE SELECT ON ALL TABLES IN SCHEMA analytics FROM analytics_readonly",
    "GRANT SELECT ON analytics.customers TO analytics_readonly",
    "GRANT SELECT ON analytics.product_categories TO analytics_readonly",
    "GRANT SELECT ON analytics.products TO analytics_readonly",
    "GRANT SELECT ON analytics.orders TO analytics_readonly",
    "GRANT SELECT ON analytics.order_items TO analytics_readonly",
]


def _upgrade(target_db: str) -> None:
    config = Config("alembic.ini")
    # version_locations 必须在 command.upgrade 之前设置（env.py 中设置太晚，无效）
    config.set_main_option("version_locations", f"migrations/versions/{target_db}")
    config.cmd_opts = SimpleNamespace(x=[f"db={target_db}"])
    command.upgrade(config, "head")
    print(f"migration ok: {target_db}")


def _grant_readonly() -> None:
    engine = analytics_owner_engine()
    with engine.begin() as conn:
        for statement in _GRANTS:
            conn.execute(text(statement))
    print("grants ok: analytics_readonly")


def main() -> None:
    _upgrade("platform")
    _upgrade("analytics")
    _grant_readonly()
    counts = run_seed()
    print(f"seed ok: {counts}")


if __name__ == "__main__":
    main()
