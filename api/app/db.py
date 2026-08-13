from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import Settings


class Engines:
    def __init__(self, settings: Settings) -> None:
        if not (
            settings.platform_database_url
            and settings.analytics_owner_database_url
            and settings.analytics_reader_database_url
        ):
            raise RuntimeError("database URLs are not configured")
        self.platform: Engine = create_engine(settings.platform_database_url, pool_pre_ping=True)
        self.analytics_owner: Engine = create_engine(
            settings.analytics_owner_database_url, pool_pre_ping=True
        )
        self.analytics_reader: Engine = create_engine(
            settings.analytics_reader_database_url, pool_pre_ping=True
        )

    def dispose(self) -> None:
        self.platform.dispose()
        self.analytics_owner.dispose()
        self.analytics_reader.dispose()
