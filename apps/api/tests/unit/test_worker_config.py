import pytest

from decisionharbor.worker.config import WorkerConfigurationError, WorkerSettings
from decisionharbor.worker.main import CONFIGURATION_EXIT_CODE, main


BASE_ENVIRONMENT = {
    "PLATFORM_DATABASE_URL": "postgresql+psycopg://platform_worker@localhost:5432/platform",
    "ANALYTICS_DATABASE_URL": "postgresql+psycopg://analytics_reader@localhost:5432/analytics",
}
WORKER_ENVIRONMENT_NAMES = (
    "PLATFORM_DATABASE_URL",
    "ANALYTICS_DATABASE_URL",
    "WORKER_ID",
    "QUERY_MAX_CONCURRENCY",
    "WORKER_LEASE_MS",
    "WORKER_HEARTBEAT_MS",
    "WORKER_POLL_MS",
    "WORKER_MAX_EXECUTION_ATTEMPTS",
)


@pytest.fixture
def worker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in WORKER_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_default_configuration_matches_the_documented_values(
    worker_environment: None,
) -> None:
    settings = WorkerSettings.from_env()

    assert settings.max_concurrency == 4
    assert settings.lease_ms == 15_000
    assert settings.heartbeat_ms == 3_000
    assert settings.poll_ms == 250
    assert settings.max_execution_attempts == 3
    assert settings.worker_id
    assert settings.platform_database_url == BASE_ENVIRONMENT["PLATFORM_DATABASE_URL"]
    assert settings.analytics_database_url == BASE_ENVIRONMENT["ANALYTICS_DATABASE_URL"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QUERY_MAX_CONCURRENCY", "0"),
        ("QUERY_MAX_CONCURRENCY", "-1"),
        ("QUERY_MAX_CONCURRENCY", "many"),
        ("WORKER_LEASE_MS", "0"),
        ("WORKER_LEASE_MS", "-15000"),
        ("WORKER_HEARTBEAT_MS", "15000"),
        ("WORKER_HEARTBEAT_MS", "30000"),
        ("WORKER_HEARTBEAT_MS", "0"),
        ("WORKER_POLL_MS", "0"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "0"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "2.5"),
    ],
)
def test_invalid_configuration_is_rejected_with_a_readable_reason(
    worker_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(WorkerConfigurationError) as caught:
        WorkerSettings.from_env()

    assert name in str(caught.value)


def test_missing_database_credentials_are_rejected(
    worker_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANALYTICS_DATABASE_URL")

    with pytest.raises(WorkerConfigurationError) as caught:
        WorkerSettings.from_env()

    assert "ANALYTICS_DATABASE_URL" in str(caught.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKER_HEARTBEAT_MS", "0"),
        ("WORKER_LEASE_MS", "3000"),
        ("QUERY_MAX_CONCURRENCY", "0"),
        ("WORKER_MAX_EXECUTION_ATTEMPTS", "not-a-number"),
    ],
)
def test_worker_process_exits_non_zero_on_invalid_configuration(
    worker_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    assert main() == CONFIGURATION_EXIT_CODE
    assert name in capsys.readouterr().err
