from concurrent.futures import ThreadPoolExecutor
import json
import os
from uuid import uuid4

import psycopg
from psycopg import rows
import pytest

from decisionharbor.executor import PostgresQueryExecutor
from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.execution import QueryRunProcessor
from decisionharbor.worker.leases import LeaseHeartbeat
from decisionharbor.worker.queue import QueryRunQueue
from decisionharbor.worker.retention import ResultRetention
from decisionharbor.worker.runtime import PlatformProbe, WorkerRuntime


pytestmark = pytest.mark.worker


# The retention window is 24 hours, measured from the `finished_at` the database
# recorded, so a run finished 25 hours ago is due and one finished 23 hours ago
# is not.
EXPIRED_HOURS_AGO = 25
RETAINED_HOURS_AGO = 23

RAW_SQL = "SELECT count(*) AS customer_count FROM customers"
COLUMNS = [{"name": "customer_count", "type": "bigint"}]
ROWS = [["100"]]


def platform_url() -> str:
    return os.environ["PLATFORM_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def insert_succeeded_run(finished_hours_ago: float) -> str:
    """A succeeded run with a stored snapshot, finished `finished_hours_ago` ago."""
    run_id = str(uuid4())
    with psycopg.connect(platform_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, returned_row_count, result_truncated,
                created_at, started_at, finished_at, duration_ms,
                execution_attempt_count, attempt_number, attempt_worker_id,
                attempt_generation, lease_expires_at, heartbeat_at
            ) VALUES (
                %s, %s, 'succeeded', 'allowed', 'policy-v1',
                5000, 500, 1, false,
                now() - %s * INTERVAL '1 hour' - INTERVAL '5 seconds',
                now() - %s * INTERVAL '1 hour' - INTERVAL '2 seconds',
                now() - %s * INTERVAL '1 hour',
                2000,
                1, 1, 'worker-test', 1, now(), now()
            )
            """,
            (run_id, RAW_SQL, finished_hours_ago, finished_hours_ago, finished_hours_ago),
        )
        connection.execute(
            """
            INSERT INTO query_run_results (
                query_run_id, result_columns, result_rows, truncated, created_at
            ) VALUES (
                %s, CAST(%s AS jsonb), CAST(%s AS jsonb), false,
                now() - %s * INTERVAL '1 hour'
            )
            """,
            (run_id, json.dumps(COLUMNS), json.dumps(ROWS), finished_hours_ago),
        )
    return run_id


def read_run(run_id: str) -> dict[str, object]:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT raw_sql, status, finished_at, duration_ms,
                   returned_row_count, result_truncated
            FROM query_runs WHERE id = %s
            """,
            (run_id,),
        ).fetchone()


def read_snapshot(run_id: str) -> dict[str, object] | None:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            "SELECT result_rows FROM query_run_results WHERE query_run_id = %s",
            (run_id,),
        ).fetchone()


def worker_runtime() -> WorkerRuntime:
    """The assembly `decisionharbor.worker.main` runs, on the worker identity."""
    settings = WorkerSettings.from_env()
    queue = QueryRunQueue(
        settings.platform_database_url,
        settings.worker_id,
        settings.lease_ms,
        settings.max_concurrency,
    )
    processor = QueryRunProcessor(
        queue,
        PostgresQueryExecutor(settings.analytics_database_url, settings.max_concurrency),
        LeaseHeartbeat(queue, settings.heartbeat_ms),
        settings.max_concurrency,
    )
    return WorkerRuntime(
        settings,
        PlatformProbe(settings.platform_database_url).check,
        processor,
        ResultRetention(settings.platform_database_url),
    )


def test_the_runtime_worker_cleans_expired_results_under_its_own_identity() -> None:
    # The worker-test container runs as `platform_worker`, the only role granted
    # DELETE on `query_run_results`, and the first poll cleans without waiting
    # for an interval, so this is the cleanup a restarted worker performs.
    run_id = insert_succeeded_run(EXPIRED_HOURS_AGO)

    worker_runtime().poll_once()

    assert read_snapshot(run_id) is None
    assert read_run(run_id)["status"] == "succeeded"


def test_an_expired_result_is_deleted_while_its_audit_facts_survive() -> None:
    run_id = insert_succeeded_run(EXPIRED_HOURS_AGO)
    facts_before = read_run(run_id)

    deleted = ResultRetention(os.environ["PLATFORM_DATABASE_URL"]).delete_expired()

    assert deleted >= 1
    assert read_snapshot(run_id) is None
    run = read_run(run_id)
    assert run["raw_sql"] == RAW_SQL
    assert run["status"] == "succeeded"
    assert run["returned_row_count"] == 1
    assert run["result_truncated"] is False
    assert run["duration_ms"] == 2_000
    assert run["finished_at"] == facts_before["finished_at"]


def test_a_second_cleanup_deletes_nothing_more() -> None:
    # The second cleaner uses its own engine, so it also stands for a process
    # that restarted in the middle of the retention window.
    expired = insert_succeeded_run(EXPIRED_HOURS_AGO)
    retained = insert_succeeded_run(RETAINED_HOURS_AGO)
    database_url = os.environ["PLATFORM_DATABASE_URL"]

    ResultRetention(database_url).delete_expired()
    second = ResultRetention(database_url).delete_expired()

    assert second == 0
    assert read_snapshot(expired) is None
    assert read_snapshot(retained) == {"result_rows": ROWS}


def test_a_result_inside_the_retention_window_is_kept() -> None:
    run_id = insert_succeeded_run(RETAINED_HOURS_AGO)

    ResultRetention(os.environ["PLATFORM_DATABASE_URL"]).delete_expired()

    assert read_snapshot(run_id) == {"result_rows": ROWS}
    assert read_run(run_id)["status"] == "succeeded"


def test_overlapping_cleanups_converge_on_the_same_stored_state() -> None:
    run_id = insert_succeeded_run(EXPIRED_HOURS_AGO)
    cleaners = [ResultRetention(os.environ["PLATFORM_DATABASE_URL"]) for _ in range(2)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted = list(pool.map(lambda cleaner: cleaner.delete_expired(), cleaners))

    assert sum(deleted) <= 1
    assert read_snapshot(run_id) is None
    assert read_run(run_id)["raw_sql"] == RAW_SQL
