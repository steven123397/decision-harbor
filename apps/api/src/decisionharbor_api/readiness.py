from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from .settings import Settings


class ReadinessChecker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._platform = create_engine(settings.platform_readiness_url, poolclass=NullPool, connect_args={"connect_timeout": settings.connect_timeout_seconds})
        self._analytics = create_engine(settings.analytics_readiness_url, poolclass=NullPool, connect_args={"connect_timeout": settings.connect_timeout_seconds})

    def dispose(self) -> None:
        self._platform.dispose()
        self._analytics.dispose()

    def check(self) -> bool:
        return self._check_one(self._platform, "platform", self.settings.platform_revision) and self._check_one(self._analytics, "analytics", self.settings.analytics_revision)

    @staticmethod
    def _check_one(engine, schema: str, expected_revision: str) -> bool:
        try:
            with engine.connect() as connection:
                revision = connection.execute(text(f"SELECT version_num FROM {schema}.alembic_version")).scalar_one_or_none()
                return revision == expected_revision
        except Exception:
            return False
