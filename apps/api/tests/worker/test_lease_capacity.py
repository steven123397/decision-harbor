from contextlib import contextmanager
from dataclasses import replace
from functools import cache
import os
import subprocess
import sys
from time import monotonic, sleep
from urllib.request import urlopen
from uuid import uuid4

import psycopg
from psycopg import rows
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.repository import row_to_query_run
from decisionharbor.worker.config import WorkerSettings
from decisionharbor.worker.queue import QueryRunQueue


pytestmark = pytest.mark.worker


TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
SAMPLE_SECONDS = 0.1
# How long an orphan's lease still has to live while a test reads the ownership
# it is about to lose. The queue only takes a run over once the lease has
# lapsed, so anything positive here makes that read race-free.
CAPTURE_SECONDS = 2

# The worker executes whatever SQL the run carries, so these never go through
# the submit policy; they are only here to keep an analytics session busy long
# enough for a lease to lapse or a heartbeat to fall due.
LONG_SQL = "SELECT count(*) FROM generate_series(1, 50000000)"
BRIEF_SQL = "SELECT count(*) FROM generate_series(1, 20000000)"
FAST_SQL = "SELECT id, customer_code FROM customers ORDER BY id LIMIT 2"

# The replica that owned a run before its lease lapsed. Every assertion that a
# takeover happened checks the owner is no longer this name.
LOST_WORKER = "worker-lost-its-lease"

RESULT = QueryResult(
    columns=(QueryColumn(name="count", type="bigint"),),
    rows=(("1",),),
    truncated=False,
)

# An execution ownership the shared capacity is charged for: one whose lease
# has not lapsed. Work that lost its lease without stopping is not one.
VALID_OWNERSHIPS = """
    SELECT count(*) FROM query_runs
    WHERE status IN ('running', 'cancelling') AND lease_expires_at > now()
"""

UNFINISHED_RUNS = """
    SELECT count(*) FROM query_runs
    WHERE id::text = ANY(%(run_ids)s)
      AND status NOT IN ('succeeded', 'failed', 'cancelled')
"""


@cache
def platform_engine() -> Engine:
    """One pool for the reads that need the row mapping the worker itself uses."""
    return create_engine(os.environ["PLATFORM_DATABASE_URL"], pool_size=4, max_overflow=0)


def platform_url() -> str:
    return os.environ["PLATFORM_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def insert_queued_run(raw_sql: str, *, statement_timeout_ms: int = 30_000) -> str:
    run_id = str(uuid4())
    with psycopg.connect(platform_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at
            ) VALUES (%s, %s, 'queued', 'allowed', 'policy-v1', %s, 500, now())
            """,
            (run_id, raw_sql, statement_timeout_ms),
        )
    return run_id


def insert_owned_run(
    raw_sql: str,
    *,
    lease_seconds: float,
    worker_id: str = LOST_WORKER,
    statement_timeout_ms: int = 30_000,
) -> str:
    """A running run one replica owns, whose lease lapses in `lease_seconds`.

    A negative `lease_seconds` is a lease that already lapsed: the run is the
    orphan another replica has to recover.
    """
    run_id = str(uuid4())
    with psycopg.connect(platform_url()) as connection:
        connection.execute(
            """
            INSERT INTO query_runs (
                id, raw_sql, status, policy_decision, policy_version,
                statement_timeout_ms, max_rows, created_at, started_at,
                execution_attempt_count, attempt_number, attempt_worker_id,
                attempt_generation, lease_expires_at, heartbeat_at
            ) VALUES (
                %s, %s, 'running', 'allowed', 'policy-v1',
                %s, 500, now(), now(),
                1, 1, %s, 1,
                now() + %s * INTERVAL '1 second', now()
            )
            """,
            (run_id, raw_sql, statement_timeout_ms, worker_id, lease_seconds),
        )
    return run_id


def read_run(run_id: str) -> dict[str, object]:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT status, started_at, execution_attempt_count, attempt_number,
                   attempt_worker_id, attempt_generation, lease_expires_at,
                   heartbeat_at
            FROM query_runs WHERE id = %s
            """,
            (run_id,),
        ).fetchone()


def owned_run(run_id: str):
    """The run as its owner sees it, mapped the way the worker maps it."""
    with platform_engine().connect() as connection:
        return row_to_query_run(
            connection.execute(
                text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).one()
        )


def read_snapshot(run_id: str) -> dict[str, object] | None:
    with psycopg.connect(platform_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            "SELECT result_rows FROM query_run_results WHERE query_run_id = %s",
            (run_id,),
        ).fetchone()


def valid_ownership_count() -> int:
    with psycopg.connect(platform_url()) as connection:
        return connection.execute(VALID_OWNERSHIPS).fetchone()[0]


def release_leases(run_ids: list[str]) -> None:
    """Let the leases of owned runs lapse, the way a replica going silent does."""
    with psycopg.connect(platform_url()) as connection:
        for run_id in run_ids:
            connection.execute(
                "UPDATE query_runs SET lease_expires_at = now() - INTERVAL '1 minute' WHERE id = %s",
                (run_id,),
            )


def sample_until(run_id: str, predicate, seconds: float = 20.0):
    """The statuses a run passed through before `predicate` held.

    Sampling stops as soon as the predicate holds, so the statuses are the ones
    a client could have observed while the transition under test took place.
    """
    statuses: list[str] = []
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        facts = read_run(run_id)
        statuses.append(facts["status"])
        if predicate(facts):
            return statuses, facts
        sleep(SAMPLE_SECONDS)
    return statuses, read_run(run_id)


def wait_until(run_id: str, predicate, seconds: float = 20.0) -> dict[str, object]:
    _, facts = sample_until(run_id, predicate, seconds)
    assert predicate(facts), f"query run {run_id} never reached the expected state: {facts}"
    return facts


def wait_for_terminal(run_id: str, seconds: float = 120.0) -> dict[str, object]:
    return wait_until(run_id, lambda facts: facts["status"] in TERMINAL_STATUSES, seconds)


def worker_queue(worker_id: str) -> QueryRunQueue:
    settings = WorkerSettings.from_env()
    return QueryRunQueue(
        settings.platform_database_url,
        worker_id,
        settings.lease_ms,
        settings.max_concurrency,
    )


@contextmanager
def second_replica(settings: WorkerSettings):
    """A second worker process, the way Compose scales the service out."""
    environment = {
        **os.environ,
        "WORKER_ID": f"worker-replica-{uuid4().hex[:8]}",
        "QUERY_MAX_CONCURRENCY": str(settings.max_concurrency),
        "WORKER_LEASE_MS": str(settings.lease_ms),
        "WORKER_HEARTBEAT_MS": str(settings.heartbeat_ms),
        "WORKER_POLL_MS": str(settings.poll_ms),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "decisionharbor.worker"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    try:
        deadline = monotonic() + 30
        while not ready and monotonic() < deadline:
            try:
                urlopen("http://127.0.0.1:8001/ready", timeout=2)
                ready = True
            except Exception:
                sleep(0.2)
        assert ready, "the second worker replica never became ready"
        yield process
    finally:
        process.terminate()
        process.wait(timeout=30)


def test_a_lost_lease_is_taken_over_without_the_run_going_back_to_queued() -> None:
    # The lease outlives these two reads by design, so the facts they capture
    # are the ownership the takeover is about to replace.
    run_id = insert_owned_run(BRIEF_SQL, lease_seconds=CAPTURE_SECONDS)
    before = read_run(run_id)

    statuses, taken = sample_until(run_id, lambda facts: facts["attempt_generation"] == 2)

    assert "queued" not in statuses, f"the run fell back to the queue: {statuses}"
    assert taken["status"] == "running"
    assert taken["attempt_worker_id"] != LOST_WORKER
    assert taken["execution_attempt_count"] == 2
    assert taken["attempt_number"] == 2
    # A takeover starts a new execution attempt for a run that was already
    # running, so the time it first started executing is not rewritten.
    assert taken["started_at"] == before["started_at"]

    terminal = wait_for_terminal(run_id)
    assert terminal["status"] == "succeeded"
    assert terminal["execution_attempt_count"] == 2


def test_a_superseded_attempt_cannot_renew_or_publish() -> None:
    # The attempt a takeover replaces keeps running until its own statement
    # timeout, so its publish arrives after the run already has a new owner.
    # The lease still has seconds to live here, which is what makes the
    # ownership read below the one that is about to be superseded.
    run_id = insert_owned_run(LONG_SQL, lease_seconds=CAPTURE_SECONDS)
    stale = owned_run(run_id)

    recovered = wait_until(run_id, lambda facts: facts["attempt_generation"] == 2)

    assert recovered["status"] == "running"
    assert recovered["attempt_worker_id"] != LOST_WORKER

    queue = worker_queue("worker-late-arrival")
    assert queue.renew_lease(stale) is False
    assert queue.publish_success(stale, RESULT) is False
    assert read_snapshot(run_id) is None
    assert read_run(run_id)["status"] == "running"
    assert read_run(run_id)["attempt_generation"] == 2

    # A lost lease and a superseded generation normally arrive together. The
    # recovered run's own facts under the generation it replaced isolate the
    # generation half of the fence, which is the only thing that tells a late
    # write from the owner's.
    superseded = replace(
        owned_run(run_id),
        attempt_number=1,
        attempt_generation=1,
        attempt_worker_id=LOST_WORKER,
    )
    assert queue.publish_success(superseded, RESULT) is False
    assert read_snapshot(run_id) is None

    assert wait_for_terminal(run_id)["status"] == "succeeded"


def test_the_heartbeat_keeps_the_lease_alive_while_the_query_runs() -> None:
    run_id = insert_queued_run(LONG_SQL)
    claimed = wait_until(run_id, lambda facts: facts["status"] == "running")

    heartbeats = {claimed["heartbeat_at"]}
    leases = {claimed["lease_expires_at"]}
    deadline = monotonic() + 60
    while monotonic() < deadline:
        facts = read_run(run_id)
        if facts["status"] in TERMINAL_STATUSES:
            break
        heartbeats.add(facts["heartbeat_at"])
        leases.add(facts["lease_expires_at"])
        sleep(SAMPLE_SECONDS)

    assert len(heartbeats) >= 2, f"the lease was never renewed: {sorted(heartbeats)}"
    assert max(leases) > min(leases), "the lease never moved forward"
    assert wait_for_terminal(run_id)["status"] == "succeeded"


def test_a_lost_lease_counts_for_nothing_while_the_shared_capacity_is_full() -> None:
    limit = WorkerSettings.from_env().max_concurrency
    held = [insert_owned_run(FAST_SQL, lease_seconds=600) for _ in range(limit - 1)]
    orphan = insert_owned_run(BRIEF_SQL, lease_seconds=-60)
    waiting = insert_queued_run(FAST_SQL)
    try:
        # Every slot but one is held, and the orphan's lapsed lease is not one
        # of them, so there is still room to recover it. Under a count that
        # charged for lapsed leases the orphan could never be claimed at all.
        recovered = wait_until(orphan, lambda facts: facts["attempt_generation"] == 2, seconds=30)

        assert recovered["status"] == "running"
        assert recovered["attempt_worker_id"] != LOST_WORKER
        # Recovering it spends the last slot again, so new work still waits.
        assert read_run(waiting)["status"] == "queued"

        assert wait_for_terminal(orphan)["status"] == "succeeded"
        assert wait_for_terminal(waiting)["status"] == "succeeded"
    finally:
        release_leases(held)
        deadline = monotonic() + 60
        while valid_ownership_count() and monotonic() < deadline:
            sleep(0.2)
        assert valid_ownership_count() == 0


def test_two_replicas_never_own_more_valid_execution_ownerships_than_the_limit() -> None:
    settings = WorkerSettings.from_env()
    limit = settings.max_concurrency
    runs = [insert_queued_run(BRIEF_SQL) for _ in range(limit * 2)]

    peak = 0
    with second_replica(settings):
        deadline = monotonic() + 180
        with psycopg.connect(platform_url()) as connection:
            while monotonic() < deadline:
                peak = max(peak, connection.execute(VALID_OWNERSHIPS).fetchone()[0])
                if not connection.execute(UNFINISHED_RUNS, {"run_ids": runs}).fetchone()[0]:
                    break
                sleep(SAMPLE_SECONDS)

    assert peak <= limit, f"{peak} valid execution ownerships exceeded the limit of {limit}"
    # The bound is not vacuous: the replicas together filled the shared
    # capacity, so what stopped them was the limit the database coordinates and
    # not a bound each replica keeps to itself.
    assert peak >= limit, f"only {peak} execution ownerships were ever held at once"
    for run_id in runs:
        assert read_run(run_id)["status"] in TERMINAL_STATUSES
