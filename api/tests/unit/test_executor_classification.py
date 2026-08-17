"""执行失败分类单元测试（#11）：错误码决定自动重试资格。

连接类失败与语句超时是基础设施错误（可自动重试）；其余数据库错误是
确定性失败。分类口径与 queue.REQUEUE_ELIGIBLE_ERROR_CODES 对齐。
"""

from __future__ import annotations

import psycopg
import pytest

from app.execute.executor import (
    QY_ANALYTICS_UNAVAILABLE,
    QY_EXECUTION_ERROR,
    QY_TIMEOUT,
    ExecutionFailure,
    execute_readonly,
)
from app.runs.queue import REQUEUE_ELIGIBLE_ERROR_CODES


class _FailingCursor:
    itersize = 0

    def __init__(self, exc: Exception):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        raise self._exc


class _Conn:
    def __init__(self, exc: Exception):
        self._exc = exc

    def cursor(self, name=None):
        return _FailingCursor(self._exc)

    def rollback(self):
        pass


def _code_for(exc: Exception) -> str:
    with pytest.raises(ExecutionFailure) as caught:
        execute_readonly("SELECT 1", conn=_Conn(exc), max_rows=10)
    return caught.value.code


def test_statement_timeout_is_retryable_infra_failure():
    assert _code_for(psycopg.errors.QueryCanceled()) == QY_TIMEOUT
    assert QY_TIMEOUT in REQUEUE_ELIGIBLE_ERROR_CODES


def test_connection_loss_is_retryable_infra_failure():
    assert (
        _code_for(psycopg.OperationalError("connection reset by peer"))
        == QY_ANALYTICS_UNAVAILABLE
    )
    assert QY_ANALYTICS_UNAVAILABLE in REQUEUE_ELIGIBLE_ERROR_CODES


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.errors.UndefinedColumn("column no_such_col does not exist"),
        psycopg.errors.SyntaxError("syntax error at or near"),
        psycopg.errors.InsufficientPrivilege("permission denied"),
        psycopg.errors.DivisionByZero("division by zero"),
    ],
)
def test_deterministic_db_errors_are_not_retryable(exc):
    assert _code_for(exc) == QY_EXECUTION_ERROR
    assert QY_EXECUTION_ERROR not in REQUEUE_ELIGIBLE_ERROR_CODES
