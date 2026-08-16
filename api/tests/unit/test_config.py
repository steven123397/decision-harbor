"""配置解析与上下界校验（design/query-governance.md 资源限制）。"""

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
        ("QUERY_MAX_CONCURRENCY", "0"),
        ("QUERY_MAX_CONCURRENCY", "100"),
    ],
)
def test_out_of_range_values_rejected(env_name, value, monkeypatch):
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_defaults_and_valid_overrides(monkeypatch):
    defaults = Settings()
    assert defaults.query_max_concurrency == 5
    assert defaults.query_max_rows == 1_000

    monkeypatch.setenv("QUERY_MAX_CONCURRENCY", "8")
    monkeypatch.setenv("QUERY_STATEMENT_TIMEOUT_MS", "2500")
    settings = Settings()
    assert settings.query_max_concurrency == 8
    assert settings.query_statement_timeout_ms == 2_500


def test_non_integer_values_rejected(monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", "many")
    with pytest.raises(ValidationError):
        Settings()
