"""platform 库迁移环境：以 platform_app 身份执行。"""

import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ["PLATFORM_DATABASE_URL"]

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
