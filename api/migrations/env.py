"""Alembic 双库环境。统一入口为 `python -m app.migrate`（在调用前按库设置 version_locations）。

platform 以 platform_app 连接，analytics 以 analytics_owner 连接；
两个库各有独立的 versions 目录与 alembic_version 表。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_db = context.get_x_argument(as_dictionary=True).get("db")
if target_db not in ("platform", "analytics"):
    raise RuntimeError("usage: alembic -x db=platform|analytics upgrade head")

settings = get_settings()
url = (
    settings.platform_url
    if target_db == "platform"
    else settings.analytics_owner_url
)
target_metadata = Base.metadata if target_db == "platform" else None


def run_migrations_online() -> None:
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
