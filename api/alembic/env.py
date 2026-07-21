import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _db_url() -> str:
    user = os.environ.get("DH_BOOTSTRAP_USER", "postgres")
    pwd = os.environ.get("DH_BOOTSTRAP_PASSWORD", "decisionharbor")
    host = os.environ.get("DH_DB_HOST", "db")
    port = os.environ.get("DH_DB_PORT", "5432")
    db = os.environ.get("DH_PLATFORM_DB", "platform")
    return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"


config.set_main_option("sqlalchemy.url", _db_url())
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
