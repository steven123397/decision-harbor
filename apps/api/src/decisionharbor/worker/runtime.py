from collections.abc import Callable
import logging
import time
from threading import Event

from sqlalchemy import create_engine, text

from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.execution import QueryRunProcessor
from decisionharbor.worker.retention import ResultRetention


LOGGER = logging.getLogger(__name__)


class PlatformProbe:
    """Confirms the worker can reach the platform queue on every poll cycle."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_size=1, max_overflow=0, pool_pre_ping=True)

    def check(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))


class WorkerRuntime:
    def __init__(
        self,
        settings: WorkerSettings,
        platform_probe: Callable[[], None],
        processor: QueryRunProcessor,
        retention: ResultRetention,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._platform_probe = platform_probe
        self._processor = processor
        self._retention = retention
        self._sleep = sleep
        self._monotonic = monotonic
        self._ready = Event()
        self._stop = Event()
        self._next_cleanup_at = 0.0

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def poll_once(self) -> None:
        try:
            self._platform_probe()
        except Exception:
            self._ready.clear()
            raise
        self._ready.set()
        self._processor.process_available()
        self._clean_expired_results()

    def _clean_expired_results(self) -> None:
        """Drop retained results that aged out, at most once per interval.

        A cleanup that cannot run is reported apart from the queue poll, so a
        maintenance failure is never mistaken for a queue failure and never
        stops the worker from claiming runs; the next attempt waits one more
        interval like any other.
        """
        now = self._monotonic()
        if now < self._next_cleanup_at:
            return
        self._next_cleanup_at = now + self._settings.cleanup_interval_ms / 1_000
        try:
            deleted = self._retention.delete_expired()
        except Exception:
            LOGGER.warning("worker %s could not clean expired results", self._settings.worker_id)
            return
        if deleted:
            LOGGER.info(
                "worker %s removed %d expired result snapshots",
                self._settings.worker_id,
                deleted,
            )

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                LOGGER.warning("worker %s could not poll the query queue", self._settings.worker_id)
            self._stop.wait(self._settings.poll_ms / 1_000)

    def stop(self) -> None:
        self._stop.set()
