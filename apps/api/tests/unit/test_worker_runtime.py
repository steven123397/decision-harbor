from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.runtime import WorkerRuntime


def settings(cleanup_interval_ms: int = 60_000) -> WorkerSettings:
    return WorkerSettings(
        worker_id="worker-test",
        platform_database_url="postgresql+psycopg://platform_worker@localhost:5432/platform",
        analytics_database_url="postgresql+psycopg://analytics_reader@localhost:5432/analytics",
        max_concurrency=4,
        lease_ms=15_000,
        heartbeat_ms=3_000,
        poll_ms=250,
        max_execution_attempts=3,
        cleanup_interval_ms=cleanup_interval_ms,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcessor:
    def __init__(self) -> None:
        self.calls = 0

    def process_next(self) -> bool:
        self.calls += 1
        return False


class FakeRetention:
    def __init__(self, *, deleted: int = 0, fail: bool = False) -> None:
        self.deleted = deleted
        self.fail = fail
        self.calls = 0

    def delete_expired(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("the platform database refused the cleanup")
        return self.deleted


def runtime(
    retention: FakeRetention,
    processor: FakeProcessor | None = None,
    *,
    cleanup_interval_ms: int = 60_000,
    clock: Clock | None = None,
) -> WorkerRuntime:
    return WorkerRuntime(
        settings(cleanup_interval_ms),
        lambda: None,
        processor or FakeProcessor(),
        retention,
        sleep=lambda _: None,
        monotonic=clock or Clock(),
    )


def test_the_first_poll_cleans_results_a_restarted_worker_may_have_missed() -> None:
    retention = FakeRetention(deleted=2)

    runtime(retention).poll_once()

    assert retention.calls == 1


def test_expired_results_are_cleaned_once_per_interval() -> None:
    retention = FakeRetention()
    clock = Clock()
    worker = runtime(retention, clock=clock)

    worker.poll_once()
    clock.advance(59)
    worker.poll_once()

    assert retention.calls == 1

    clock.advance(1)
    worker.poll_once()

    assert retention.calls == 2


def test_a_failed_cleanup_leaves_the_worker_ready_and_keeps_polling() -> None:
    retention = FakeRetention(fail=True)
    processor = FakeProcessor()
    worker = runtime(retention, processor)

    worker.poll_once()
    worker.poll_once()

    assert worker.ready is True
    assert processor.calls == 2
    assert retention.calls == 1
