"""analytics 库迁移环境：以 analytics_owner 身份执行（凭据仅迁移与 seed 持有）。"""

import os

from alembic import context
from sqlalchemy import create_engine, text

url = os.environ["ANALYTICS_MIGRATION_URL"]

engine = create_engine(url)

# 版本表位于契约 schema，须先于 Alembic 建版本表存在
with engine.begin() as connection:
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table_schema="analytics",
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
