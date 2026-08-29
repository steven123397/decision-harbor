import logging
import signal
import sys

from decisionharbor.executor import PostgresQueryExecutor
from decisionharbor.worker.config import WorkerConfigurationError, WorkerSettings
from decisionharbor.worker.execution import QueryRunProcessor
from decisionharbor.worker.health import WorkerHealthServer
from decisionharbor.worker.queue import QueryRunQueue
from decisionharbor.worker.runtime import PlatformProbe, WorkerRuntime


HEALTH_PORT = 8001
CONFIGURATION_EXIT_CODE = 2

LOGGER = logging.getLogger("decisionharbor.worker")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = WorkerSettings.from_env()
    except WorkerConfigurationError as error:
        print(f"worker configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT_CODE

    processor = QueryRunProcessor(
        QueryRunQueue(settings.platform_database_url, settings.worker_id, settings.lease_ms),
        PostgresQueryExecutor(settings.analytics_database_url, settings.max_concurrency),
    )
    runtime = WorkerRuntime(settings, PlatformProbe(settings.platform_database_url).check, processor)
    health_server = WorkerHealthServer(HEALTH_PORT, is_ready=lambda: runtime.ready)
    health_server.start()
    LOGGER.info(
        "worker %s started with concurrency=%d lease=%dms heartbeat=%dms poll=%dms attempts=%d",
        settings.worker_id,
        settings.max_concurrency,
        settings.lease_ms,
        settings.heartbeat_ms,
        settings.poll_ms,
        settings.max_execution_attempts,
    )

    def stop(_signum: int, _frame: object) -> None:
        runtime.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        runtime.run()
    finally:
        health_server.stop()
    return 0
