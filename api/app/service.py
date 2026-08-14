"""Query submission and lifecycle orchestration."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from .config import settings
from .executor import EXEC_INTERNAL, ExecutionError, ExecResult, execute
from .policy import check as policy_check
from .store import QueryRunStore


class ResultCache:
    def __init__(self, max_entries: int = 100, ttl_seconds: int = 3600) -> None:
        self._data: OrderedDict[str, tuple[float, ExecResult]] = OrderedDict()
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds

    def put(self, run_id: str, result: ExecResult) -> None:
        now = time.monotonic()
        self._data[run_id] = (now + self.ttl_seconds, result)
        self._data.move_to_end(run_id)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def get(self, run_id: str) -> ExecResult | None:
        now = time.monotonic()
        item = self._data.get(run_id)
        if item is None:
            return None
        expires, result = item
        if now > expires:
            del self._data[run_id]
            return None
        return result


class QueryService:
    def __init__(self, store: QueryRunStore, cache: ResultCache, catalog) -> None:
        self.store = store
        self.cache = cache
        self.catalog = catalog

    def submit(self, sql: str):
        decision = policy_check(sql, allowed_tables=self.catalog.table_names)
        if not decision.allowed:
            return self.store.create_rejected(sql, decision.code, decision.message)
        run = self.store.create_running(sql)
        threading.Thread(target=self._run, args=(run.id, sql), daemon=True).start()
        return run

    def _run(self, run_id: str, sql: str) -> None:
        start = time.monotonic()
        try:
            result = execute(
                settings.analytics_reader_dsn,
                sql,
                settings.query_timeout_ms,
                settings.query_row_limit,
            )
        except ExecutionError as exc:
            self.store.set_failed(run_id, exc.code, exc.summary, int((time.monotonic() - start) * 1000))
            return
        except Exception as exc:  # noqa: BLE001
            self.store.set_failed(run_id, EXEC_INTERNAL, str(exc), int((time.monotonic() - start) * 1000))
            return
        self.cache.put(run_id, result)
        self.store.set_succeeded(run_id, result.row_count, result.duration_ms)

    def get(self, run_id: str):
        run = self.store.get(run_id)
        if run is None:
            return None, None
        result = self.cache.get(run_id) if run.status == "succeeded" else None
        return run, result
