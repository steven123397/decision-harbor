from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DH_", env_file=".env", extra="ignore")

    db_host: str = "db"
    db_port: int = 5432
    bootstrap_user: str = "postgres"
    bootstrap_password: str = "decisionharbor"
    platform_db: str = "platform"
    analytics_db: str = "analytics"
    platform_writer_user: str = "dh_platform_writer"
    platform_writer_password: str = "dh_platform_writer"
    analytics_reader_user: str = "dh_analytics_reader"
    analytics_reader_password: str = "dh_analytics_reader"
    statement_timeout_ms: int = 30000
    row_limit: int = 1000
    analytics_seed_dir: str = "/datasets/sales-analytics-v1"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
