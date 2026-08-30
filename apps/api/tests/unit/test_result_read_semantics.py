from datetime import datetime, timedelta, timezone

import pytest

from decisionharbor.domain import (
    RESULT_EXPIRED,
    RESULT_NOT_READY,
    RESULT_RETENTION,
    RESULT_UNAVAILABLE,
    QueryColumn,
    QueryResult,
    QueryRun,
    result_read_failure,
)


FINISHED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
SNAPSHOT = QueryResult(
    columns=(QueryColumn(name="customer_count", type="bigint"),),
    rows=(("100",),),
    truncated=False,
)


def run(status: str) -> QueryRun:
    return QueryRun(
        id="75e24c21-416c-4bd8-a37d-68667f4ec753",
        raw_sql="SELECT count(*) AS customer_count FROM customers",
        status=status,
        policy_decision="allowed" if status != "rejected" else "rejected",
        policy_version="policy-v1",
        referenced_objects=("analytics.customers",),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=1 if status == "succeeded" else None,
        result_truncated=False if status == "succeeded" else None,
        error_code="query_timeout" if status == "failed" else None,
        error_summary="The query exceeded its time limit." if status == "failed" else None,
        created_at=FINISHED_AT - timedelta(seconds=4),
        started_at=FINISHED_AT - timedelta(seconds=3),
        finished_at=FINISHED_AT,
        duration_ms=3_000,
    )


def test_a_result_is_retained_for_twenty_four_hours() -> None:
    assert RESULT_RETENTION == timedelta(hours=24)


@pytest.mark.parametrize("status", ["received", "queued", "running", "cancelling"])
@pytest.mark.parametrize(("has_snapshot", "expired"), [(False, False), (True, False), (True, True)])
def test_a_run_that_has_not_finished_is_never_more_than_not_ready(
    status: str, has_snapshot: bool, expired: bool
) -> None:
    failure = result_read_failure(
        run(status), has_snapshot=has_snapshot, expired=expired
    )

    assert failure == RESULT_NOT_READY


@pytest.mark.parametrize("status", ["rejected", "failed", "cancelled"])
def test_a_terminal_run_that_never_produced_a_result_is_unavailable(status: str) -> None:
    failure = result_read_failure(run(status), has_snapshot=False, expired=False)

    assert failure == RESULT_UNAVAILABLE


def test_a_succeeded_run_inside_the_retention_window_is_readable() -> None:
    failure = result_read_failure(run("succeeded"), has_snapshot=True, expired=False)

    assert failure is None


def test_a_succeeded_run_past_the_retention_window_is_expired() -> None:
    failure = result_read_failure(run("succeeded"), has_snapshot=True, expired=True)

    assert failure == RESULT_EXPIRED


def test_a_cleared_snapshot_is_still_expired_rather_than_unavailable() -> None:
    # Retention, not the presence of a row, decides the outcome: a succeeded run
    # whose snapshot the cleaner already removed still reports the result aged
    # out, so a client can tell an old result from a run that never had one.
    failure = result_read_failure(run("succeeded"), has_snapshot=False, expired=True)

    assert failure == RESULT_EXPIRED


@pytest.mark.parametrize("status", ["rejected", "failed", "cancelled"])
def test_a_terminal_run_without_a_result_never_expires(status: str) -> None:
    failure = result_read_failure(run(status), has_snapshot=False, expired=True)

    assert failure == RESULT_UNAVAILABLE


def test_a_succeeded_run_inside_the_window_whose_snapshot_is_missing_is_unavailable() -> None:
    failure = result_read_failure(run("succeeded"), has_snapshot=False, expired=False)

    assert failure == RESULT_UNAVAILABLE
