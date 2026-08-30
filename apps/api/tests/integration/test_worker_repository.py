import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from decisionharbor.config import ApiSettings, WorkerSettings
from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository, StateConflict
from decisionharbor.result_snapshot import RESULT_MAX_BYTES
from decisionharbor.worker import QueryWorker


pytestmark = pytest.mark.integration


@pytest.fixture
def ownership_database_urls():
    database_name = f"ownership_{uuid4().hex}"
    admin_root_url = (
        os.environ["TEST_ADMIN_DATABASE_URL"]
        .replace("postgresql://", "postgresql+psycopg://")
    )
    admin_engine = create_engine(admin_root_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    admin_database_url = admin_root_url.rsplit("/", 1)[0] + f"/{database_name}"
    migration_config = Config("alembic-platform.ini")
    migration_config.set_main_option("sqlalchemy.url", admin_database_url)
    command.upgrade(migration_config, "head")
    api_database_url = (
        ApiSettings.from_env().platform_database_url.rsplit("/", 1)[0]
        + f"/{database_name}"
    )
    worker_database_url = (
        WorkerSettings.from_env().platform_database_url.rsplit("/", 1)[0]
        + f"/{database_name}"
    )
    try:
        yield api_database_url, worker_database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE "{database_name}"'))
        admin_engine.dispose()


def queued_run(repository: QueryRunRepository, suffix: str = ""):
    run = repository.create(
        f"SELECT count(*) FROM customers /* ownership {suffix} */",
        "policy-v1",
        5_000,
        500,
    ).query_run
    return repository.transition(
        run.id,
        "received",
        status="queued",
        policy_decision="allowed",
        referenced_objects=("analytics.customers",),
    )


def test_postgres_executor_enforces_the_exact_snapshot_byte_limit() -> None:
    settings = WorkerSettings.from_env()
    executor = PostgresQueryExecutor(settings.analytics_database_url, 1)
    empty_snapshot_size = len(
        json.dumps(
            {
                "columns": [{"name": "value", "type": "text"}],
                "rows": [[""]],
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    exact_value_size = RESULT_MAX_BYTES - empty_snapshot_size

    exact = executor.execute(
        f"SELECT repeat('x', {exact_value_size})::text AS value",
        statement_timeout_ms=5_000,
        max_rows=500,
    )
    with pytest.raises(ExecutionFailure) as caught:
        executor.execute(
            f"SELECT repeat('x', {exact_value_size + 1})::text AS value",
            statement_timeout_ms=5_000,
            max_rows=500,
        )

    assert len(exact.rows[0][0]) == exact_value_size
    assert exact.truncated is False
    assert caught.value.code == "result_too_large"


def test_postgres_executor_keeps_a_stable_prefix_and_reuses_the_connection_after_failure() -> None:
    settings = WorkerSettings.from_env()
    executor = PostgresQueryExecutor(settings.analytics_database_url, 1)

    truncated = executor.execute(
        "SELECT repeat('x', 600000)::text AS value FROM generate_series(1, 2) ORDER BY generate_series",
        statement_timeout_ms=5_000,
        max_rows=500,
    )
    with pytest.raises(ExecutionFailure) as caught:
        executor.execute(
            """
            SELECT CASE ordinal WHEN 1 THEN 'kept' ELSE repeat('x', 1048577) END::text AS value
            FROM generate_series(1, 2) AS ordinal
            ORDER BY ordinal
            """,
            statement_timeout_ms=5_000,
            max_rows=500,
        )
    reused = executor.execute(
        "SELECT 'reused'::text AS value",
        statement_timeout_ms=5_000,
        max_rows=500,
    )

    assert truncated.rows == (("x" * 600_000,),)
    assert truncated.truncated is True
    assert caught.value.code == "result_too_large"
    assert reused.rows == (("reused",),)
    assert reused.truncated is False


def test_postgres_executor_can_request_cancellation_of_the_active_query() -> None:
    settings = WorkerSettings.from_env()
    executor = PostgresQueryExecutor(settings.analytics_database_url, 1)

    assert executor.cancel() is False
    with ThreadPoolExecutor(max_workers=1) as pool:
        execution = pool.submit(
            executor.execute,
            "SELECT pg_sleep(5) IS NULL AS slept FROM analytics.customers LIMIT 1",
            10_000,
            500,
        )
        for _ in range(100):
            if executor.cancel():
                break
            sleep(0.01)
        else:
            pytest.fail("executor did not expose its active analytics connection")

        with pytest.raises(ExecutionFailure) as caught:
            execution.result(timeout=2)

    assert caught.value.code == "query_timeout"
    assert executor.cancel() is False


def test_claim_records_an_attempt_and_an_unexpired_lease_cannot_be_stolen(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued = queued_run(api_repository, "exclusive")

    first = worker_repository.claim_next("worker-a", max_concurrency=4, lease_ms=15_000)
    second = worker_repository.claim_next("worker-b", max_concurrency=4, lease_ms=15_000)

    assert first is not None
    assert first.query_run.id == queued.id
    assert first.query_run.status == "running"
    assert first.worker_id == "worker-a"
    assert first.generation == 1
    assert first.lease_expires_at > first.heartbeat_at
    assert second is None

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            attempt = connection.execute(
                text(
                    """
                    SELECT worker_id, generation, heartbeat_at, lease_expires_at
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid)
                    """
                ),
                {"run_id": queued.id},
            ).one()
        assert attempt == (
            first.worker_id,
            first.generation,
            first.heartbeat_at,
            first.lease_expires_at,
        )
    finally:
        engine.dispose()
    assert worker_repository.release_ownership(first) is True


def test_an_expired_running_run_is_taken_over_without_returning_to_the_queue(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued = queued_run(api_repository, "takeover")

    first = worker_repository.claim_next(
        "worker-a",
        max_concurrency=4,
        lease_ms=10,
        max_execution_attempts=3,
    )
    assert first is not None
    sleep(0.02)
    second = worker_repository.claim_next(
        "worker-b",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=3,
    )

    assert second is not None
    assert second.query_run.id == queued.id
    assert second.query_run.status == "running"
    assert second.query_run.started_at == first.query_run.started_at
    assert second.worker_id == "worker-b"
    assert second.generation == 2

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            attempts = connection.execute(
                text(
                    """
                    SELECT generation, released_at IS NOT NULL, release_reason
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid)
                    ORDER BY generation
                    """
                ),
                {"run_id": queued.id},
            ).all()
        assert attempts == [(1, True, "lease_expired"), (2, False, None)]
    finally:
        engine.dispose()


def test_an_expired_run_fails_stably_when_execution_attempts_are_exhausted(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued = queued_run(api_repository, "attempts-exhausted")

    first = worker_repository.claim_next(
        "worker-a",
        max_concurrency=4,
        lease_ms=10,
        max_execution_attempts=2,
    )
    assert first is not None
    sleep(0.02)
    second = worker_repository.claim_next(
        "worker-b",
        max_concurrency=4,
        lease_ms=10,
        max_execution_attempts=2,
    )
    assert second is not None
    sleep(0.02)

    assert worker_repository.claim_next(
        "worker-c",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=2,
    ) is None
    failed = api_repository.get(queued.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "execution_attempts_exhausted"
    assert failed.error_summary == "Automatic execution attempts were exhausted."

    assert worker_repository.claim_next(
        "worker-d",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=2,
    ) is None
    assert api_repository.get(queued.id) == failed

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            attempts = connection.execute(
                text(
                    """
                    SELECT generation, release_reason
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid)
                    ORDER BY generation
                    """
                ),
                {"run_id": queued.id},
            ).all()
        assert attempts == [(1, "lease_expired"), (2, "attempts_exhausted")]
    finally:
        engine.dispose()


def test_analytics_unavailable_releases_the_attempt_and_reuses_the_governed_run(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued = queued_run(api_repository, "analytics-unavailable")

    first = worker_repository.claim_next(
        "worker-a",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=3,
    )
    assert first is not None
    assert worker_repository.release_for_retry(first, "analytics_unavailable") is True

    second = worker_repository.claim_next(
        "worker-b",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=3,
    )
    assert second is not None
    assert second.query_run.id == queued.id
    assert second.query_run.raw_sql == queued.raw_sql
    assert second.query_run.policy_decision == queued.policy_decision
    assert second.query_run.referenced_objects == queued.referenced_objects
    assert second.query_run.status == "running"
    assert second.generation == 2

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            attempts = connection.execute(
                text(
                    """
                    SELECT generation, worker_id, release_reason
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid)
                    ORDER BY generation
                    """
                ),
                {"run_id": queued.id},
            ).all()
        assert attempts == [
            (1, "worker-a", "analytics_unavailable"),
            (2, "worker-b", None),
        ]
    finally:
        engine.dispose()


def test_a_second_worker_completes_the_same_run_after_analytics_unavailable(
    ownership_database_urls,
) -> None:
    class UnavailableExecutor:
        def execute(self, raw_sql: str, statement_timeout_ms: int, max_rows: int) -> QueryResult:
            raise ExecutionFailure(
                "analytics_unavailable",
                "The analytics database is unavailable.",
            )

        def cancel(self) -> bool:
            return False

    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    queued = queued_run(api_repository, "worker-recovery")
    settings = WorkerSettings.from_env()
    first_worker = QueryWorker(
        QueryRunRepository(worker_database_url),
        UnavailableExecutor(),
        worker_id="worker-a",
        max_concurrency=4,
        lease_ms=15_000,
        heartbeat_ms=1_000,
        max_execution_attempts=3,
    )
    second_worker = QueryWorker(
        QueryRunRepository(worker_database_url),
        PostgresQueryExecutor(settings.analytics_database_url, 1),
        worker_id="worker-b",
        max_concurrency=4,
        lease_ms=15_000,
        heartbeat_ms=1_000,
        max_execution_attempts=3,
    )

    assert first_worker.process_one() is True
    after_first_attempt = api_repository.get(queued.id)
    assert after_first_attempt is not None
    assert after_first_attempt.status == "running"

    assert second_worker.process_one() is True
    succeeded = api_repository.get(queued.id)
    assert succeeded is not None
    assert succeeded.status == "succeeded"
    assert api_repository.get_result(queued.id) is not None

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            attempts = connection.execute(
                text(
                    """
                    SELECT generation, worker_id, release_reason
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid)
                    ORDER BY generation
                    """
                ),
                {"run_id": queued.id},
            ).all()
        assert attempts == [
            (1, "worker-a", "analytics_unavailable"),
            (2, "worker-b", "succeeded"),
        ]
    finally:
        engine.dispose()


def test_a_non_current_generation_cannot_publish(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued = queued_run(api_repository, "fencing")
    stale = worker_repository.claim_next(
        "worker-a",
        max_concurrency=4,
        lease_ms=10,
        max_execution_attempts=3,
    )
    assert stale is not None
    sleep(0.02)
    current = worker_repository.claim_next(
        "worker-b",
        max_concurrency=4,
        lease_ms=15_000,
        max_execution_attempts=3,
    )
    assert current is not None
    assert current.generation == stale.generation + 1
    result = QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=(("100",),),
        truncated=False,
    )
    with pytest.raises(StateConflict):
        worker_repository.publish_failure(stale, "internal_error", "Late stale failure.")
    with pytest.raises(StateConflict):
        worker_repository.publish_success(stale, result)

    unchanged = api_repository.get(queued.id)
    assert unchanged is not None
    assert unchanged.status == "running"
    assert unchanged.error_code is None
    assert api_repository.get_result(queued.id) is None
    assert worker_repository.publish_success(current, result).status == "succeeded"


def test_a_released_execution_attempt_cannot_publish(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued_run(api_repository, "released-attempt")
    ownership = worker_repository.claim_next("worker-a", max_concurrency=4, lease_ms=15_000)
    assert ownership is not None
    result = QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=(("100",),),
        truncated=False,
    )
    engine = create_engine(worker_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE query_execution_attempts
                    SET released_at = now(), release_reason = 'worker_stopped'
                    WHERE query_run_id = CAST(:run_id AS uuid)
                      AND generation = :generation
                    """
                ),
                {"run_id": ownership.query_run.id, "generation": ownership.generation},
            )
    finally:
        engine.dispose()

    with pytest.raises(StateConflict):
        worker_repository.publish_success(ownership, result)

    unchanged = api_repository.get(ownership.query_run.id)
    assert unchanged is not None
    assert unchanged.status == "running"
    assert api_repository.get_result(ownership.query_run.id) is None


def test_success_publication_rolls_back_if_the_snapshot_insert_fails(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued_run(api_repository, "atomic-rollback")
    ownership = worker_repository.claim_next("worker-a", max_concurrency=4, lease_ms=15_000)
    assert ownership is not None
    original = QueryResult(
        columns=(QueryColumn(name="value", type="text"),),
        rows=(("original",),),
        truncated=False,
    )
    replacement = replace(original, rows=(("replacement",),))
    engine = create_engine(worker_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO query_results (query_run_id, columns_json, rows_json, truncated)
                    VALUES (CAST(:run_id AS uuid), '[{"name":"value","type":"text"}]', '[["original"]]', false)
                    """
                ),
                {"run_id": ownership.query_run.id},
            )

        with pytest.raises(IntegrityError):
            worker_repository.publish_success(ownership, replacement)

        with engine.connect() as connection:
            attempt = connection.execute(
                text(
                    """
                    SELECT released_at, release_reason
                    FROM query_execution_attempts
                    WHERE query_run_id = CAST(:run_id AS uuid) AND generation = :generation
                    """
                ),
                {"run_id": ownership.query_run.id, "generation": ownership.generation},
            ).one()
    finally:
        engine.dispose()

    unchanged = api_repository.get(ownership.query_run.id)
    assert unchanged is not None
    assert unchanged.status == "running"
    assert api_repository.get_result(ownership.query_run.id) == original
    assert attempt == (None, None)


@pytest.mark.parametrize(
    ("raw_sql", "expected_code"),
    [
        ("SELECT repeat('x', 1048577)::text AS value", "result_too_large"),
        ("SELECT '{}'::jsonb AS value", "unsupported_result_type"),
    ],
)
def test_worker_publishes_stable_snapshot_failures_without_result_content(
    ownership_database_urls,
    raw_sql: str,
    expected_code: str,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    run = api_repository.create(raw_sql, "policy-v1", 5_000, 500).query_run
    api_repository.transition(
        run.id,
        "received",
        status="queued",
        policy_decision="allowed",
        referenced_objects=(),
    )
    settings = WorkerSettings.from_env()
    worker = QueryWorker(
        QueryRunRepository(worker_database_url),
        PostgresQueryExecutor(settings.analytics_database_url, 1),
        worker_id="snapshot-worker",
        max_concurrency=1,
        lease_ms=15_000,
        heartbeat_ms=1_000,
    )

    assert worker.process_one() is True

    failed = api_repository.get(run.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == expected_code
    assert api_repository.get_result(run.id) is None


def test_heartbeat_extends_current_ownership_and_prevents_takeover(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued_run(api_repository, "heartbeat")
    claim = worker_repository.claim_next("worker-a", max_concurrency=4, lease_ms=50)
    assert claim is not None
    sleep(0.03)

    renewed = worker_repository.renew_lease(claim, lease_ms=100)
    sleep(0.04)

    assert renewed is not None
    assert renewed.lease_expires_at > claim.lease_expires_at
    assert worker_repository.claim_next("worker-b", max_concurrency=4, lease_ms=100) is None
    assert worker_repository.release_ownership(renewed) is True


def test_two_workers_share_one_global_capacity_limit(ownership_database_urls) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repositories = (
        QueryRunRepository(worker_database_url),
        QueryRunRepository(worker_database_url),
    )
    runs = [queued_run(api_repository, f"capacity-{index}") for index in range(6)]

    def claim(index: int):
        return worker_repositories[index % 2].claim_next(
            f"worker-{index % 2}",
            max_concurrency=4,
            lease_ms=15_000,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        claims = list(executor.map(claim, range(6)))

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 4
    assert len({claim.query_run.id for claim in claimed}) == 4
    assert {claim.query_run.id for claim in claimed} <= {run.id for run in runs}

    engine = create_engine(api_database_url)
    try:
        with engine.connect() as connection:
            valid_ownerships = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM query_runs
                    WHERE status = 'running' AND lease_expires_at > now()
                    """
                )
            ).scalar_one()
        assert valid_ownerships == 4
    finally:
        engine.dispose()
    for claim in claimed:
        repository_index = int(claim.worker_id.removeprefix("worker-"))
        assert worker_repositories[repository_index].release_ownership(claim) is True


def test_two_query_workers_observe_one_current_owner_at_the_global_limit(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    settings = WorkerSettings.from_env()
    api_repository = QueryRunRepository(api_database_url)
    slow_sql = (
        "SELECT count(*) FROM analytics.customers "
        "CROSS JOIN LATERAL pg_sleep(0.3)"
    )
    for index in range(2):
        run = api_repository.create(f"{slow_sql} /* dual-worker-{index} */", "policy-v1", 5_000, 500).query_run
        api_repository.transition(
            run.id,
            "received",
            status="queued",
            policy_decision="allowed",
            referenced_objects=("analytics.customers",),
        )
    workers = [
        QueryWorker(
            QueryRunRepository(worker_database_url),
            PostgresQueryExecutor(settings.analytics_database_url, 1),
            worker_id=f"query-worker-{index}",
            max_concurrency=1,
            lease_ms=1_000,
            heartbeat_ms=100,
        )
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker.process_one) for worker in workers]
        engine = create_engine(api_database_url)
        try:
            for _ in range(20):
                with engine.connect() as connection:
                    ownership = connection.execute(
                        text(
                            """
                            SELECT count(*), count(DISTINCT owner_worker_id)
                            FROM query_runs
                            WHERE status = 'running' AND lease_expires_at > now()
                            """
                        )
                    ).one()
                if ownership == (1, 1):
                    break
                sleep(0.02)
            assert ownership == (1, 1)
            assert sorted(future.result() for future in futures) == [False, True]
        finally:
            engine.dispose()
