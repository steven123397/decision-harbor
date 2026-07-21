from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

platform_engine = create_engine(settings.platform_database_url, pool_pre_ping=True)
PlatformSession = sessionmaker(bind=platform_engine)


def check_platform_ready() -> bool:
    try:
        with platform_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
