"""只读执行连接池：有界、复用、带会话级超时设置。

设计承诺（ADR-0005/0007）：
只读执行使用独立小连接池，与平台写入隔离。
"""

from __future__ import annotations

import threading

import psycopg


class ReadOnlyPool:
    """极简有界连接池：min_size 惰性建立，绝不超 max_size。

    首轮单实例部署、并发由服务层容量信号量保证不超过池上限，
    因此用条件变量实现阻塞获取即可，不引入 psycopg_pool 依赖。
    """

    def __init__(self, dsn: str, *, max_size: int, statement_timeout_ms: int):
        self._dsn = dsn
        self._max_size = max_size
        self._timeout_ms = statement_timeout_ms
        self._idle: list[psycopg.Connection] = []
        self._created = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    @property
    def max_size(self) -> int:
        return self._max_size

    def acquire(self) -> psycopg.Connection:
        with self._cond:
            while True:
                if self._idle:
                    return self._idle.pop()
                if self._created < self._max_size:
                    self._created += 1
                    break
                self._cond.wait()
        # 连接建立放在锁外（慢操作）
        try:
            conn = self._new_connection()
        except Exception:
            with self._cond:
                self._created -= 1
                self._cond.notify()
            raise
        return conn

    def release(self, conn: psycopg.Connection) -> None:
        healthy = not conn.closed
        if healthy:
            try:
                conn.rollback()
            except psycopg.Error:
                healthy = False
        if healthy:
            with self._cond:
                self._idle.append(conn)
                self._cond.notify()
        else:
            # 不健康的连接必须关闭底层 socket，只减计数会泄漏连接。
            self.discard(conn)

    def discard(self, conn: psycopg.Connection) -> None:
        try:
            conn.close()
        except psycopg.Error:
            pass
        with self._cond:
            self._created -= 1
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            for conn in self._idle:
                try:
                    conn.close()
                except psycopg.Error:
                    pass
            self._idle.clear()
            self._created = 0
            self._cond.notify_all()

    def _new_connection(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                # SET 不接受绑定参数，用标识符安全拼接整数毫秒值。
                cur.execute(f"SET statement_timeout = {int(self._timeout_ms)}")
            # SET 是事务性语句：提交后才在连接生命周期内稳定生效，
            # 不会被执行器结尾的 rollback 撤销。
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn
