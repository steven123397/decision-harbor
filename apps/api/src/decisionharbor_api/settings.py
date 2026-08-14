from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    platform_runtime_url: str
    analytics_runtime_url: str
    platform_readiness_url: str
    analytics_readiness_url: str
    platform_revision: str = "platform_0001"
    analytics_revision: str = "analytics_0001"
    max_sql_bytes: int = 64 * 1024
    max_result_rows: int = 10_000
    max_result_columns: int = 128
    max_result_bytes: int = 4 * 1024 * 1024
    statement_timeout_ms: int = 5_000
    lock_timeout_ms: int = 1_000
    connect_timeout_seconds: int = 2
    max_concurrent_queries: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            platform_runtime_url=_env(
                "PLATFORM_RUNTIME_URL",
                "postgresql+psycopg://platform_writer:platform_writer@localhost:5432/platform",
            ),
            analytics_runtime_url=_env(
                "ANALYTICS_RUNTIME_URL",
                "postgresql+psycopg://analytics_reader:analytics_reader@localhost:5432/analytics",
            ),
            platform_readiness_url=_env(
                "PLATFORM_READINESS_URL",
                "postgresql+psycopg://platform_readiness:platform_readiness@localhost:5432/platform",
            ),
            analytics_readiness_url=_env(
                "ANALYTICS_READINESS_URL",
                "postgresql+psycopg://analytics_readiness:analytics_readiness@localhost:5432/analytics",
            ),
            platform_revision=_env("PLATFORM_REVISION", "platform_0001"),
            analytics_revision=_env("ANALYTICS_REVISION", "analytics_0001"),
        )
