import pytest

from decisionharbor.config import WorkerSettings


def worker_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    environment = {
        "PLATFORM_DATABASE_URL": "postgresql+psycopg://platform_worker:x@postgres:5432/platform",
        "ANALYTICS_DATABASE_URL": "postgresql+psycopg://analytics_reader:x@postgres:5432/analytics",
        "ANALYTICS_READINESS_DATABASE_URL": "postgresql+psycopg://analytics_readiness:x@postgres:5432/analytics",
        "DATASET_ROOT": "/app/datasets/sales-analytics-v1",
    }
    environment.update(overrides)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


def test_worker_settings_defaults_match_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_env(monkeypatch)

    settings = WorkerSettings.from_env()

    assert settings.max_concurrency == 4
    assert settings.lease_ms == 15_000
    assert settings.heartbeat_ms == 3_000
    assert settings.poll_ms == 250
    assert settings.max_execution_attempts == 3
    assert settings.http_port == 8001


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QUERY_MAX_CONCURRENCY", "0"),
        ("QUERY_MAX_CONCURRENCY", "not-a-number"),
        ("WORKER_LEASE_MS", "0"),
        ("WORKER_HEARTBEAT_MS", "-1"),
        ("WORKER_POLL_MS", "0"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "0"),
    ],
)
def test_non_positive_worker_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    worker_env(monkeypatch, **{name: value})

    with pytest.raises(ValueError):
        WorkerSettings.from_env()


def test_heartbeat_must_be_strictly_less_than_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_env(monkeypatch, WORKER_HEARTBEAT_MS="15000", WORKER_LEASE_MS="15000")
    with pytest.raises(ValueError):
        WorkerSettings.from_env()

    worker_env(monkeypatch, WORKER_HEARTBEAT_MS="16000", WORKER_LEASE_MS="15000")
    with pytest.raises(ValueError):
        WorkerSettings.from_env()
