from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from .seed import SeedConflict, seed_dataset


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def upgrade(database_url: str, script_location: Path, schema: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("prepend_sys_path", str(script_location.parent.parent / "src"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("version_table_schema", schema)
    command.upgrade(config, "head")


def _api_root() -> Path:
    configured = os.environ.get("DECISIONHARBOR_APP_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def main() -> None:
    api_root = _api_root()
    upgrade(_required("PLATFORM_ADMIN_URL"), api_root / "migrations" / "platform", "platform")
    upgrade(_required("ANALYTICS_ADMIN_URL"), api_root / "migrations" / "analytics", "analytics")
    result = seed_dataset(_required("ANALYTICS_ADMIN_URL"))
    print(f"analytics seed: {result}")


if __name__ == "__main__":
    try:
        main()
    except SeedConflict as error:
        raise SystemExit(f"seed conflict: {error}") from error
