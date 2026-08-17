"""服务层并发容量上限（design/query-governance.md 资源限制）。"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from app.runs.service import QueryRunService


class _Decision:
    allowed = True
    code = None
    message = None


def _make_service(max_concurrency: int, capacity_wait_seconds: float) -> QueryRunService:
    return QueryRunService(
        session_factory=None,
        readonly_dsn="postgresql://ignored",
        statement_timeout_ms=10_000,
        max_rows=10,
        sql_max_length=1000,
        allowed_tables=frozenset({"customers"}),
        max_concurrency=max_concurrency,
        capacity_wait_seconds=capacity_wait_seconds,
    )


class _FakePool:
    """只满足服务层接缝：acquire/release 可任意调用，不连数据库。"""

    def __init__(self):
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return object()

    def release(self, _conn):
        pass

    def close(self):
        pass


def test_submit_rejects_when_capacity_exhausted():
    """容量满时，后续提交得到稳定失败码而不是第 N+1 条连接。"""
    service = _make_service(max_concurrency=1, capacity_wait_seconds=0.1)
    pool = _FakePool()
    service._pool = pool  # 已有惰性属性,直接注入

    hold = threading.Event()
    results = {}

    class _FakeResult:
        columns = []
        rows = []
        row_count = 0
        truncated = False
        duration_ms = 1

    def slow_execute(sql, **kwargs):
        hold.wait(timeout=5)
        return _FakeResult()

    def fake_evaluate(sql, **kwargs):
        return _Decision()

    finalized: list[dict] = []

    def fake_finalize(run_id, **fields):
        finalized.append({"id": run_id, **fields})
        # 返回一个足够 run_to_dict 使用的最小记录
        class R:
            pass

        r = R()
        r.id = run_id
        r.state = fields.get("state")
        r.sql = "SELECT"
        r.rejection_code = fields.get("rejection_code")
        r.rejection_message = fields.get("rejection_message")
        r.row_count = None
        r.truncated = False
        r.duration_ms = None
        r.error_code = fields.get("error_code")
        r.error_message = fields.get("error_message")
        r.created_at = None
        r.finished_at = None
        return r

    def fake_create(sql):
        class R:
            pass

        r = R()
        r.id = 1
        return r

    with (
        patch.object(service, "_evaluate", fake_evaluate),
        patch.object(service, "_create_run", fake_create),
        patch.object(service, "_execute", slow_execute),
        patch.object(service, "_finalize", fake_finalize),
    ):
        first = threading.Thread(
            target=lambda: results.setdefault("first", service.submit("SELECT 1"))
        )
        first.start()
        deadline = time.monotonic() + 5
        while pool.acquired < 1 and time.monotonic() < deadline:
            time.sleep(0.02)  # 确认第一个已真实占住容量与连接
        assert pool.acquired == 1
        results["second"] = service.submit("SELECT 2")
        hold.set()
        first.join(timeout=5)
        # 容量已释放：第三个提交在补丁上下文内走成功路径
        results["third"] = service.submit("SELECT 3")

    # 容量耗尽是执行失败（资源繁忙），不是策略拒绝
    assert results["second"]["outcome"] == "failed"
    assert results["second"]["run"]["error_code"] == "QY_CAPACITY_EXCEEDED"
    assert results["second"]["run"]["rejection_code"] is None
    assert any(f.get("error_code") == "QY_CAPACITY_EXCEEDED" for f in finalized)
    # third 成功复用释放后的容量（第二条连接）
    assert results["third"]["outcome"] == "succeeded"
    assert pool.acquired == 2


def test_pool_created_once_and_shared_under_concurrency():
    """并发首请求只创建一个池，acquire/release 全部落在同一实例上。

    回归：惰性赋值会让多个线程各自建池，acquire 与 release 读到
    不同实例，导致计数失真与 close() 遗漏。
    """
    created = []

    class _TrackingPool:
        def __init__(self, *args, **kwargs):
            created.append(self)
            self.acquires = 0
            self.releases = 0

        def acquire(self):
            self.acquires += 1
            return object()

        def release(self, conn):
            self.releases += 1

        def close(self):
            pass

    class _FakeResult:
        columns = []
        rows = []
        row_count = 0
        truncated = False
        duration_ms = 1

    def fake_finalize(self, run_id, **fields):
        class R:
            pass

        r = R()
        r.id = run_id
        r.state = fields.get("state")
        r.sql = "SELECT"
        r.rejection_code = fields.get("rejection_code")
        r.rejection_message = fields.get("rejection_message")
        r.row_count = fields.get("row_count")
        r.truncated = False
        r.duration_ms = fields.get("duration_ms")
        r.error_code = fields.get("error_code")
        r.error_message = fields.get("error_message")
        r.created_at = None
        r.finished_at = None
        return r

    with (
        patch("app.runs.service.ReadOnlyPool", _TrackingPool),
        patch.object(QueryRunService, "_evaluate", lambda self, sql: _Decision()),
        patch.object(QueryRunService, "_create_run", lambda self, sql: 1),
        patch.object(QueryRunService, "_finalize", fake_finalize),
        patch.object(QueryRunService, "_execute", lambda self, sql, conn: _FakeResult()),
    ):
        service = _make_service(max_concurrency=4, capacity_wait_seconds=0.5)
        threads = [
            threading.Thread(target=lambda i=i: service.submit(f"SELECT {i}"))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert len(created) == 1
    assert created[0].acquires == 8
    assert created[0].releases == 8


def test_submit_maps_acquire_failure_to_stable_failed():
    """连接建立失败映射为稳定失败码，不抛异常、不留 running 记录。"""
    service = _make_service(max_concurrency=2, capacity_wait_seconds=0.1)

    class _BrokenPool:
        def acquire(self):
            raise RuntimeError("connection refused")

        def release(self, conn):
            pass

        def close(self):
            pass

    service._pool = _BrokenPool()

    def fake_evaluate(sql, **kwargs):
        return _Decision()

    finalized: list[dict] = []

    def fake_finalize(run_id, **fields):
        finalized.append({"id": run_id, **fields})

        class R:
            pass

        r = R()
        r.id = run_id
        r.state = fields.get("state")
        r.sql = "SELECT 1"
        r.rejection_code = None
        r.rejection_message = None
        r.row_count = None
        r.truncated = False
        r.duration_ms = None
        r.error_code = fields.get("error_code")
        r.error_message = fields.get("error_message")
        r.created_at = None
        r.finished_at = None
        return r

    def fake_create(sql):
        class R:
            pass

        r = R()
        r.id = 7
        return r

    with (
        patch.object(service, "_evaluate", fake_evaluate),
        patch.object(service, "_create_run", fake_create),
        patch.object(service, "_finalize", fake_finalize),
    ):
        outcome = service.submit("SELECT 1")

    assert outcome["outcome"] == "failed"
    assert outcome["run"]["error_code"] == "QY_ANALYTICS_UNAVAILABLE"
    assert finalized and finalized[0]["state"] == "failed"
