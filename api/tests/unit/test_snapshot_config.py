"""配置与快照序列化上限单元测试（#13）：QUERY_MAX_ROWS 默认 500 且
运行时钳制不得超过 500；快照字节达 1 MiB 即截断标记。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.execute.executor import ColumnDef, ExecutionResult
from app.runs.queue import SNAPSHOT_MAX_BYTES, build_snapshot


def test_query_max_rows_defaults_to_500():
    assert Settings().query_max_rows == 500


@pytest.mark.parametrize("value", ["501", "1000", "50000"])
def test_query_max_rows_above_500_rejected(value, monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", value)
    with pytest.raises(ValidationError):
        Settings()


def test_query_max_rows_500_valid(monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", "500")
    assert Settings().query_max_rows == 500


def _result(rows: list[list]) -> ExecutionResult:
    return ExecutionResult(
        columns=[ColumnDef(name="x", type="text")],
        rows=rows,
        row_count=len(rows),
        truncated=False,
        duration_ms=1,
    )


def test_build_snapshot_reports_size_bytes():
    snap = build_snapshot(_result([["a"], ["b"]]))
    assert snap["size_bytes"] > 0
    assert snap["rows"] == [["a"], ["b"]]


def test_snapshot_max_bytes_is_1mib():
    assert SNAPSHOT_MAX_BYTES == 1_048_576
