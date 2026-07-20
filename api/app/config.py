"""运行配置：全部来自环境变量，默认值与设计文档一致。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    platform_url: str
    analytics_owner_url: str
    analytics_readonly_url: str
    statement_timeout_ms: int
    max_rows: int
    datasets_dir: str


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings(
        platform_url=_required("PLATFORM_DATABASE_URL"),
        analytics_owner_url=_required("ANALYTICS_OWNER_DATABASE_URL"),
        analytics_readonly_url=_required("ANALYTICS_READONLY_DATABASE_URL"),
        statement_timeout_ms=int(os.environ.get("STATEMENT_TIMEOUT_MS", "5000")),
        max_rows=int(os.environ.get("MAX_ROWS", "1000")),
        datasets_dir=os.environ.get("DATASETS_DIR", "/datasets"),
    )
