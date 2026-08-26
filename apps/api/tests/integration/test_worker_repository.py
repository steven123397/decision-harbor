from concurrent.futures import ThreadPoolExecutor
import os
from dataclasses import replace
from time import sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from decisionharbor.config import ApiSettings, WorkerSettings
from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.executor import PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository, StateConflict
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


def test_a_non_current_generation_cannot_publish(
    ownership_database_urls,
) -> None:
    api_database_url, worker_database_url = ownership_database_urls
    api_repository = QueryRunRepository(api_database_url)
    worker_repository = QueryRunRepository(worker_database_url)
    queued_run(api_repository, "fencing")
    current = worker_repository.claim_next("worker-a", max_concurrency=4, lease_ms=15_000)
    assert current is not None
    stale = replace(current, generation=current.generation - 1)
    result = QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=(("100",),),
        truncated=False,
    )
    with pytest.raises(StateConflict):
        worker_repository.publish_success(stale, result)
    assert worker_repository.publish_success(current, result).status == "succeeded"


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
