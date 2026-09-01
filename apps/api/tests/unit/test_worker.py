import logging
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from decisionharbor.config import WorkerSettings
from decisionharbor.worker import (
    QueryWorker,
    create_worker_ready_app,
    main as worker_main,
    should_release_for_retry,
)


def test_worker_readiness_endpoints_use_the_unified_envelope() -> None:
    app = create_worker_ready_app(lambda: True)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"data": {"status": "ready"}, "error": None}


def test_worker_readiness_reports_not_ready_safely() -> None:
    app = create_worker_ready_app(lambda: False)
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"
    assert client.get("/health").status_code == 200


def test_worker_main_exits_nonzero_on_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_LEASE_MS", "0")

    with pytest.raises(SystemExit) as caught:
        worker_main()

    assert caught.value.code == 2


def test_only_analytics_unavailable_triggers_automatic_retry() -> None:
    assert should_release_for_retry("analytics_unavailable", generation=1, max_execution_attempts=3) is True
    for code in (
        "query_timeout",
        "query_semantic_error",
        "result_too_large",
        "unsupported_result_type",
        "internal_error",
    ):
        assert should_release_for_retry(code, generation=1, max_execution_attempts=3) is False


def test_retry_decision_respects_the_execution_attempt_cap() -> None:
    assert should_release_for_retry("analytics_unavailable", generation=2, max_execution_attempts=3) is True
    assert should_release_for_retry("analytics_unavailable", generation=3, max_execution_attempts=3) is False


def test_worker_ready_returns_promptly_when_the_probe_hangs() -> None:
    release = Event()

    def blocked_readiness_check() -> bool:
        release.wait(timeout=1)
        return False

    app = create_worker_ready_app(blocked_readiness_check, readiness_deadline_seconds=0.01)
    try:
        with TestClient(app) as client:
            started_at = monotonic()
            response = client.get("/ready")
        elapsed = monotonic() - started_at
    finally:
        release.set()

    assert elapsed < 0.5
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"


# 端口 1 上的回环地址立即拒绝连接：三个数据库角色共用同一不可达地址即可触发维护失败。
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://platform_worker:refused@127.0.0.1:1/platform"


def unreachable_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        platform_database_url=UNREACHABLE_DATABASE_URL,
        analytics_database_url=UNREACHABLE_DATABASE_URL,
        analytics_readiness_database_url=UNREACHABLE_DATABASE_URL,
        dataset_root=Path("."),
        max_concurrency=4,
        lease_ms=1_000,
        heartbeat_ms=50,
        poll_ms=20,
        max_execution_attempts=3,
        http_port=8001,
        cleanup_interval_ms=300_000,
    )


def test_worker_loop_failures_do_not_leak_stacks_or_raw_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = QueryWorker(unreachable_worker_settings(), worker_id="log-sanitizer")
    stop = Event()
    with caplog.at_level(logging.WARNING, logger="decisionharbor.worker"):
        loop = Thread(target=worker.run_forever, args=(stop,), daemon=True)
        loop.start()
        sleep(0.5)
        stop.set()
        loop.join(timeout=5)
    assert not loop.is_alive()

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("worker iteration failed" in record.getMessage() for record in warnings)
    assert any("lease renewal failed" in record.getMessage() for record in warnings)
    for record in warnings:
        # 默认日志不得泄漏堆栈、数据库原始消息或 DSN。
        assert record.exc_info is None
        assert "connection refused" not in record.getMessage()
        assert "Traceback" not in record.getMessage()
        assert "refused@127.0.0.1:1" not in record.getMessage()
