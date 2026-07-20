"""Runtime configuration from environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    platform_database_url: str = (
        "postgresql+psycopg://platform_app:platform_app@localhost:5432/platform"
    )
    analytics_readonly_url: str = (
        "postgresql+psycopg://analytics_readonly:analytics_readonly@localhost:5432/analytics"
    )
    analytics_migrator_url: str = (
        "postgresql+psycopg://analytics_migrator:analytics_migrator@localhost:5432/analytics"
    )
    # Superuser / bootstrap DSN for creating DBs and roles (ops only).
    postgres_admin_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    )

    statement_timeout_ms: int = 5000
    max_result_rows: int = 1000
    dataset_dir: str = "/datasets/sales-analytics-v1"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
