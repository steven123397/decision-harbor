from datetime import datetime, timedelta, timezone

import pytest

from decisionharbor.domain import (
    RESULT_EXPIRED,
    RESULT_NOT_READY,
    RESULT_RETENTION,
    RESULT_UNAVAILABLE,
    QueryRun,
    result_read_failure,
)


FINISHED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def build_query_run(status: str) -> QueryRun:
    succeeded = status == "succeeded"
    return QueryRun(
        id="75e24c21-416c-4bd8-a37d-68667f4ec753",
        raw_sql="SELECT 1",
        status=status,
        policy_decision="allowed" if status != "rejected" else "rejected",
        policy_version="policy-v1",
        referenced_objects=(),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=1 if succeeded else None,
        result_truncated=False if succeeded else None,
        error_code=None if succeeded else "query_timeout",
        error_summary=None if succeeded else "The query exceeded its time limit.",
        created_at=FINISHED_AT - timedelta(seconds=4),
        started_at=FINISHED_AT - timedelta(seconds=3) if status not in {"rejected", "queued"} else None,
        finished_at=FINISHED_AT if status in {"succeeded", "failed", "rejected", "cancelled"} else None,
        duration_ms=3_000 if status in {"succeeded", "failed", "rejected", "cancelled"} else None,
    )


def test_result_retention_is_twenty_four_hours() -> None:
    assert RESULT_RETENTION == timedelta(hours=24)


@pytest.mark.parametrize("status", ["received", "queued", "running", "cancelling"])
def test_unfinished_runs_are_not_ready_even_if_a_snapshot_row_is_present(status: str) -> None:
    assert result_read_failure(build_query_run(status), has_snapshot=True, expired=True) == RESULT_NOT_READY


@pytest.mark.parametrize("status", ["rejected", "failed", "cancelled"])
def test_terminal_runs_without_results_are_unavailable(status: str) -> None:
    assert result_read_failure(build_query_run(status), has_snapshot=False, expired=True) == RESULT_UNAVAILABLE


def test_succeeded_run_inside_retention_with_snapshot_is_readable() -> None:
    assert result_read_failure(build_query_run("succeeded"), has_snapshot=True, expired=False) is None


def test_succeeded_run_at_retention_boundary_is_expired() -> None:
    assert result_read_failure(build_query_run("succeeded"), has_snapshot=True, expired=True) == RESULT_EXPIRED


def test_expired_succeeded_run_stays_expired_after_cleanup_removes_snapshot() -> None:
    assert result_read_failure(build_query_run("succeeded"), has_snapshot=False, expired=True) == RESULT_EXPIRED


def test_succeeded_run_with_missing_snapshot_before_expiry_is_unavailable() -> None:
    assert result_read_failure(build_query_run("succeeded"), has_snapshot=False, expired=False) == RESULT_UNAVAILABLE
