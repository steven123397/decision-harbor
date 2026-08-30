import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from decisionharbor.executor import PostgresQueryExecutor
from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.execution import QueryRunProcessor
from decisionharbor.worker.health import WorkerHealthServer
from decisionharbor.worker.queue import QueryRunQueue
from decisionharbor.worker.retention import ResultRetention
from decisionharbor.worker.runtime import PlatformProbe, WorkerRuntime


pytestmark = pytest.mark.worker


def runtime(settings: WorkerSettings) -> WorkerRuntime:
    processor = QueryRunProcessor(
        QueryRunQueue(settings.platform_database_url, settings.worker_id, settings.lease_ms),
        PostgresQueryExecutor(settings.analytics_database_url, 1),
    )
    return WorkerRuntime(
        settings,
        PlatformProbe(settings.platform_database_url).check,
        processor,
        ResultRetention(settings.platform_database_url),
    )


def test_worker_process_reports_health_and_readiness_in_the_runtime() -> None:
    worker_url = os.environ["WORKER_URL"]

    health = json.loads(urlopen(f"{worker_url}/health", timeout=5).read())
    ready = json.loads(urlopen(f"{worker_url}/ready", timeout=5).read())

    assert health == {"data": {"status": "ok"}, "error": None}
    assert ready == {"data": {"status": "ready"}, "error": None}


def test_worker_becomes_ready_after_a_successful_queue_poll() -> None:
    worker = runtime(WorkerSettings.from_env())

    assert worker.ready is False

    worker.poll_once()

    assert worker.ready is True


def test_health_endpoints_follow_the_worker_readiness_state() -> None:
    readiness = {"ready": False}
    server = WorkerHealthServer(0, is_ready=lambda: readiness["ready"])
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"

        assert json.loads(urlopen(f"{base}/health", timeout=5).read()) == {
            "data": {"status": "ok"},
            "error": None,
        }
        with pytest.raises(HTTPError) as caught:
            urlopen(f"{base}/ready", timeout=5)
        assert caught.value.code == 503
        assert json.loads(caught.value.read())["error"]["code"] == "worker_not_ready"

        readiness["ready"] = True
        assert json.loads(urlopen(f"{base}/ready", timeout=5).read()) == {
            "data": {"status": "ready"},
            "error": None,
        }

    finally:
        server.stop()
