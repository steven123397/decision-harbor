"""数据库引擎与会话：平台库读写身份与分析库只读身份严格分离。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def psycopg_url(sqlalchemy_url: str) -> str:
    """SQLAlchemy URL 转 psycopg 连接串。"""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def platform_engine() -> Engine:
    return create_engine(get_settings().platform_url, pool_pre_ping=True)


@lru_cache
def analytics_owner_engine() -> Engine:
    return create_engine(get_settings().analytics_owner_url, pool_pre_ping=True)


@lru_cache
def _platform_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=platform_engine(), expire_on_commit=False)


def platform_session() -> Session:
    return _platform_session_factory()()
