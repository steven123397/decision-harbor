"""Application settings and connection DSN builders."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: str
    postgres_user: str
    postgres_password: str
    platform_db: str
    analytics_db: str
    dh_admin_user: str
    dh_admin_password: str
    platform_writer_user: str
    platform_writer_password: str
    analytics_reader_user: str
    analytics_reader_password: str
    query_timeout_ms: int
    query_row_limit: int
    max_sql_bytes: int
    cors_origin: str
    dataset_dir: str

    def _dsn(self, user: str, password: str, database: str) -> str:
        return (
            f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{self.db_host}:{self.db_port}/{database}"
        )

    @property
    def superuser_dsn(self) -> str:
        return self._dsn(self.postgres_user, self.postgres_password, "postgres")

    @property
    def platform_admin_dsn(self) -> str:
        return self._dsn(self.dh_admin_user, self.dh_admin_password, self.platform_db)

    @property
    def analytics_admin_dsn(self) -> str:
        return self._dsn(self.dh_admin_user, self.dh_admin_password, self.analytics_db)

    @property
    def platform_writer_dsn(self) -> str:
        return self._dsn(self.platform_writer_user, self.platform_writer_password, self.platform_db)

    @property
    def analytics_reader_dsn(self) -> str:
        return self._dsn(self.analytics_reader_user, self.analytics_reader_password, self.analytics_db)


def get_settings() -> Settings:
    default_dataset = str(Path(__file__).resolve().parents[2] / "datasets" / "sales-analytics-v1")
    return Settings(
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_port=os.environ.get("DB_PORT", "5432"),
        postgres_user=os.environ.get("POSTGRES_USER", "postgres"),
        postgres_password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        platform_db=os.environ.get("PLATFORM_DB", "platform"),
        analytics_db=os.environ.get("ANALYTICS_DB", "analytics"),
        dh_admin_user=os.environ.get("DH_ADMIN_USER", "dh_admin"),
        dh_admin_password=os.environ.get("DH_ADMIN_PASSWORD", "dh_admin"),
        platform_writer_user=os.environ.get("PLATFORM_WRITER_USER", "platform_writer"),
        platform_writer_password=os.environ.get("PLATFORM_WRITER_PASSWORD", "platform_writer"),
        analytics_reader_user=os.environ.get("ANALYTICS_READER_USER", "analytics_reader"),
        analytics_reader_password=os.environ.get("ANALYTICS_READER_PASSWORD", "analytics_reader"),
        query_timeout_ms=int(os.environ.get("QUERY_TIMEOUT_MS", "30000")),
        query_row_limit=int(os.environ.get("QUERY_ROW_LIMIT", "10000")),
        max_sql_bytes=int(os.environ.get("MAX_SQL_BYTES", "65536")),
        cors_origin=os.environ.get("CORS_ORIGIN", "http://localhost:5173"),
        dataset_dir=os.environ.get("DATASET_DIR", default_dataset),
    )


settings = get_settings()
