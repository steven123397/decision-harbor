"""配置解析与上下界校验（ADR-0005/0016 资源限制）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("QUERY_SQL_MAX_LENGTH", "0"),
        ("QUERY_SQL_MAX_LENGTH", "2000000"),
        ("QUERY_STATEMENT_TIMEOUT_MS", "10"),
        ("QUERY_STATEMENT_TIMEOUT_MS", "120000"),
        ("QUERY_MAX_ROWS", "0"),
        ("QUERY_MAX_ROWS", "100000"),
        ("WORKER_LEASE_SECONDS", "0"),
        ("WORKER_LEASE_SECONDS", "601"),
        ("WORKER_POLL_INTERVAL_MS", "10"),
        ("WORKER_POLL_INTERVAL_MS", "20000"),
        ("RESULT_RETENTION_HOURS", "0"),
        ("RESULT_RETENTION_HOURS", "721"),
    ],
)
def test_out_of_range_values_rejected(env_name, value, monkeypatch):
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_defaults_and_valid_overrides(monkeypatch):
    defaults = Settings()
    assert defaults.query_max_rows == 1_000
    assert defaults.worker_lease_seconds == 30
    assert defaults.result_retention_hours == 24

    monkeypatch.setenv("WORKER_LEASE_SECONDS", "60")
    monkeypatch.setenv("QUERY_STATEMENT_TIMEOUT_MS", "2500")
    settings = Settings()
    assert settings.worker_lease_seconds == 60
    assert settings.query_statement_timeout_ms == 2_500


def test_non_integer_values_rejected(monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", "many")
    with pytest.raises(ValidationError):
        Settings()
