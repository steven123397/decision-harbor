"""Database engines and session factories."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.platform_engine = create_engine(
            self.settings.platform_database_url,
            pool_pre_ping=True,
        )
        self.analytics_readonly_engine = create_engine(
            self.settings.analytics_readonly_url,
            pool_pre_ping=True,
        )
        self.PlatformSession = sessionmaker(
            bind=self.platform_engine, autoflush=False, autocommit=False
        )

    def platform_session(self) -> Generator[Session, None, None]:
        session = self.PlatformSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def reset_db() -> None:
    global _db
    _db = None
