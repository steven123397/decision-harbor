"""连接池单元测试：不健康连接释放必须关闭底层连接。"""

from __future__ import annotations

import psycopg

from app.execute.pool import ReadOnlyPool


class _BrokenConn:
    """rollback 抛错模拟连接半途损坏；close 应被调用。"""

    def __init__(self):
        self.closed = False
        self.close_calls = 0

    def rollback(self):
        raise psycopg.Error("connection broken")

    def close(self):
        self.close_calls += 1
        self.closed = True


def test_release_closes_unhealthy_connection_and_returns_capacity():
    pool = ReadOnlyPool("postgresql://ignored", max_size=1, statement_timeout_ms=1000)
    conn = _BrokenConn()
    with pool._cond:  # 模拟该连接已由池创建
        pool._created = 1

    pool.release(conn)

    assert conn.close_calls == 1, "不健康连接必须关闭底层 socket"
    with pool._cond:
        assert pool._created == 0
        assert pool._idle == []


def test_release_healthy_connection_returns_to_idle():
    pool = ReadOnlyPool("postgresql://ignored", max_size=1, statement_timeout_ms=1000)

    class _HealthyConn:
        closed = False

        def rollback(self):
            pass

    conn = _HealthyConn()
    with pool._cond:
        pool._created = 1

    pool.release(conn)

    with pool._cond:
        assert pool._idle == [conn]
        assert pool._created == 1
