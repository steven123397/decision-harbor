"""运行时配置：全部来自环境变量，带本地默认值。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # platform 库：Alembic 用 owner，服务运行时用 app。
    platform_owner_url: str = (
        "postgresql+psycopg://platform_owner:platform_owner@localhost:5432/platform"
    )
    platform_app_url: str = (
        "postgresql+psycopg://platform_app:platform_app@localhost:5432/platform"
    )

    # analytics 库：psycopg 原生 DSN（seed 与只读执行器）。
    analytics_owner_dsn: str = (
        "postgresql://analytics_owner:analytics_owner@localhost:5432/analytics"
    )
    analytics_readonly_dsn: str = (
        "postgresql://analytics_readonly:analytics_readonly@localhost:5432/analytics"
    )

    dataset_dir: str = "datasets/sales-analytics-v1"

    # 查询治理资源限制，语义见 docs/design/query-governance.md。
    query_sql_max_length: int = 100_000
    query_statement_timeout_ms: int = 10_000
    query_max_rows: int = 1_000
    query_pool_size: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
