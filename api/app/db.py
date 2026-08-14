"""双数据库连接管理。

platform 库经 SQLAlchemy 2.0 引擎（审计读写）；
analytics 只读执行经 psycopg 直连（流式取数与超时控制更直接）。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_platform_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def create_platform_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
