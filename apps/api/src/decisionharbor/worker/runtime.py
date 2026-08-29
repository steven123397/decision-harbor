from collections.abc import Callable
import logging
import time
from threading import Event

from sqlalchemy import create_engine, text

from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.execution import QueryRunProcessor


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._platform_probe = platform_probe
        self._processor = processor
        self._sleep = sleep
        self._ready = Event()
        self._stop = Event()

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
        self._processor.process_next()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                LOGGER.warning("worker %s could not poll the query queue", self._settings.worker_id)
            self._stop.wait(self._settings.poll_ms / 1_000)

    def stop(self) -> None:
        self._stop.set()
