import logging
from threading import Event, Lock, Thread

from decisionharbor.domain import QueryRun
from decisionharbor.worker.queue import QueryRunQueue


LOGGER = logging.getLogger(__name__)


class LeaseHeartbeat:
    """Keeps the lease of every query run this worker is executing alive.

    A replica that stops sending its heartbeat loses its ownership, which is
    what lets another replica take the run over. Renewal is fenced by the
    execution attempt, so a run that was taken over is dropped here rather than
    extended behind its new owner's back.
    """

    def __init__(self, queue: QueryRunQueue, interval_ms: int) -> None:
        self._queue = queue
        self._interval = interval_ms / 1_000
        self._owned: dict[str, QueryRun] = {}
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self.run, name="worker-lease-heartbeat", daemon=True)

    @property
    def owned_count(self) -> int:
        """How many executions this worker is currently the owner of."""
        with self._lock:
            return len(self._owned)

    def track(self, run: QueryRun) -> None:
        """Start renewing the lease of an execution this worker claimed."""
        with self._lock:
            self._owned[run.id] = run

    def release(self, run: QueryRun) -> None:
        """Stop renewing an execution this worker is done with."""
        with self._lock:
            self._owned.pop(run.id, None)

    def renew_once(self) -> None:
        """Renew every tracked lease, dropping the ownerships already lost.

        An ownership the database refuses to renew belongs to another replica
        now, so it is dropped instead of retried. A renewal that cannot be
        issued at all is a transport failure rather than a lost ownership, so
        the run stays tracked and the next beat tries again.
        """
        with self._lock:
            owned = list(self._owned.values())
        for run in owned:
            try:
                renewed = self._queue.renew_lease(run)
            except Exception:
                LOGGER.warning("worker could not renew the lease of query run %s", run.id)
                continue
            if not renewed:
                self.release(run)

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            self.renew_once()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
