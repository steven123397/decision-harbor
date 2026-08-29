import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.health import WorkerHealthServer
from decisionharbor.worker.runtime import PlatformProbe, WorkerRuntime


pytestmark = pytest.mark.worker


def test_worker_process_reports_health_and_readiness_in_the_runtime() -> None:
    worker_url = os.environ["WORKER_URL"]

    health = json.loads(urlopen(f"{worker_url}/health", timeout=5).read())
    ready = json.loads(urlopen(f"{worker_url}/ready", timeout=5).read())

    assert health == {"data": {"status": "ok"}, "error": None}
    assert ready == {"data": {"status": "ready"}, "error": None}


def test_worker_becomes_ready_after_a_successful_queue_poll() -> None:
    settings = WorkerSettings.from_env()
    runtime = WorkerRuntime(settings, PlatformProbe(settings.platform_database_url).check)

    assert runtime.ready is False

    runtime.poll_once()

    assert runtime.ready is True


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
