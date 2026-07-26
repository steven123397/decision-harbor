"""数据库引擎：platform 写引擎与 analytics 只读引擎，进程内各一份。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import get_settings


@lru_cache(maxsize=1)
def platform_engine() -> Engine:
    return create_engine(get_settings().platform_database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def analytics_reader_engine() -> Engine:
    return create_engine(get_settings().analytics_reader_url, pool_pre_ping=True)
