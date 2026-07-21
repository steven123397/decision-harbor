from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    platform_database_url: str = "postgresql+psycopg://platform_app:platform_pass@localhost:5432/platform"
    analytics_database_url: str = "postgresql+psycopg://analytics_reader:reader_pass@localhost:5432/analytics"
    analytics_admin_database_url: str = "postgresql+psycopg://analytics_admin:admin_pass@localhost:5432/analytics"
    query_statement_timeout_ms: int = 30000
    query_max_rows: int = 1000


settings = Settings()
