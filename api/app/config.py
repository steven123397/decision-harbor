"""运行配置：全部来自环境变量，治理参数默认值见 docs/design/query-governance.md。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    platform_database_url: str
    analytics_reader_url: str
    query_timeout_ms: int
    query_row_limit: int
    query_max_sql_length: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        platform_database_url=os.environ["PLATFORM_DATABASE_URL"],
        analytics_reader_url=os.environ["ANALYTICS_READER_URL"],
        query_timeout_ms=int(os.environ.get("QUERY_TIMEOUT_MS", "5000")),
        query_row_limit=int(os.environ.get("QUERY_ROW_LIMIT", "1000")),
        query_max_sql_length=int(os.environ.get("QUERY_MAX_SQL_LENGTH", "100000")),
    )
