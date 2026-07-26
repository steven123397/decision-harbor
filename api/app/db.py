"""数据库引擎：platform 写引擎与 analytics 只读引擎，进程内各一份。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import get_settings


def make_engine(url: str) -> Engine:
    """建引擎，并给建立连接和执行语句都设上界。

    连接超时让数据库完全不可达时快速失败而不是无限等待，`/ready` 因此能在有限时间
    内给出明确结论。语句超时是连接级兜底，用于没有自带超时的语句（就绪探测、审计
    写入）；查询执行由执行器每事务 `SET LOCAL statement_timeout` 单独管控，该设置
    覆盖连接级取值，不受此处影响。
    """
    settings = get_settings()
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_s,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
    )


@lru_cache(maxsize=1)
def platform_engine() -> Engine:
    return make_engine(get_settings().platform_database_url)


@lru_cache(maxsize=1)
def analytics_reader_engine() -> Engine:
    return make_engine(get_settings().analytics_reader_url)
