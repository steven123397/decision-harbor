from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    platform_database_url: str = ""
    analytics_owner_database_url: str = ""
    analytics_reader_database_url: str = ""
    dataset_dir: str = "/datasets/sales-analytics-v1"
    query_max_sql_chars: int = 20_000
    query_statement_timeout_ms: int = 5_000
    query_max_rows: int = 1_000
    skip_bootstrap: bool = False


def load_settings() -> Settings:
    return Settings()
