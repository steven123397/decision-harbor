from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import get_settings


def _dsn(user: str, pwd: str, db: str) -> str:
    s = get_settings()
    return f"postgresql+psycopg://{user}:{pwd}@{s.db_host}:{s.db_port}/{db}"


def platform_engine() -> Engine:
    """平台写入身份：仅访问 platform 库的 query_runs。"""
    s = get_settings()
    return create_engine(
        _dsn(s.platform_writer_user, s.platform_writer_password, s.platform_db),
        pool_pre_ping=True,
        future=True,
    )


def analytics_engine() -> Engine:
    """分析只读身份：仅访问 analytics 库的五张表。"""
    s = get_settings()
    return create_engine(
        _dsn(s.analytics_reader_user, s.analytics_reader_password, s.analytics_db),
        pool_pre_ping=True,
        future=True,
    )


def bootstrap_analytics_engine() -> Engine:
    """引导身份：仅用于 seed，建立 analytics schema/表并授权。"""
    s = get_settings()
    return create_engine(
        _dsn(s.bootstrap_user, s.bootstrap_password, s.analytics_db),
        pool_pre_ping=True,
        future=True,
    )
