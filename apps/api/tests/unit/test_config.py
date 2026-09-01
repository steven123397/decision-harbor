import pytest

from decisionharbor.config import WorkerSettings


def worker_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> WorkerSettings:
    environment = {
        "PLATFORM_WORKER_DATABASE_URL": "postgresql+psycopg://worker@platform/platform",
        "ANALYTICS_DATABASE_URL": "postgresql+psycopg://reader@analytics/analytics",
        **overrides,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return WorkerSettings.from_env()


def test_worker_settings_use_the_ownership_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = worker_settings(monkeypatch)

    assert settings.max_concurrency == 4
    assert settings.lease_ms == 15_000
    assert settings.heartbeat_ms == 3_000
    assert settings.poll_ms == 250
    assert settings.max_execution_attempts == 3
    assert settings.cleanup_interval_ms == 60_000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QUERY_MAX_CONCURRENCY", "0"),
        ("WORKER_LEASE_MS", "0"),
        ("WORKER_HEARTBEAT_MS", "0"),
        ("WORKER_POLL_MS", "0"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "0"),
        ("WORKER_CLEANUP_INTERVAL_MS", "0"),
        ("WORKER_LEASE_MS", "not-an-integer"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "not-an-integer"),
    ],
)
def test_worker_settings_reject_non_positive_or_non_integer_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=name):
        worker_settings(monkeypatch, **{name: value})


@pytest.mark.parametrize(
    ("heartbeat_ms", "lease_ms"),
    [(15_000, 15_000), (15_001, 15_000)],
)
def test_worker_settings_require_heartbeat_before_lease_expiry(
    monkeypatch: pytest.MonkeyPatch,
    heartbeat_ms: int,
    lease_ms: int,
) -> None:
    with pytest.raises(ValueError, match="WORKER_HEARTBEAT_MS must be less than WORKER_LEASE_MS"):
        worker_settings(
            monkeypatch,
            WORKER_HEARTBEAT_MS=str(heartbeat_ms),
            WORKER_LEASE_MS=str(lease_ms),
        )
