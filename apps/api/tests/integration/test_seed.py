import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from decisionharbor.bootstrap import ROOT, _database_url, _migrate
from decisionharbor.dataset import TABLE_LOAD_ORDER, load_dataset
from decisionharbor.seed import SeedConflict, seed_dataset


pytestmark = pytest.mark.integration


def test_seed_is_repeatable_and_conflicts_without_overwriting_rows() -> None:
    admin_url = os.environ["TEST_ADMIN_DATABASE_URL"]
    database = f"seed_test_{uuid4().hex}"
    analytics_url = _database_url(admin_url, database)
    dataset = load_dataset(Path(os.environ["DATASET_ROOT"]))

    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        _migrate(
            ROOT / "alembic-analytics.ini",
            _database_url(admin_url, database, sqlalchemy=True),
        )

        assert seed_dataset(analytics_url, dataset) == "seeded"
        assert seed_dataset(analytics_url, dataset) == "unchanged"
        expected_counts = _counts(analytics_url)

        with psycopg.connect(analytics_url) as connection:
            connection.execute(
                "UPDATE maintenance.dataset_seeds SET contract_sha256 = %s",
                ("0" * 64,),
            )
        with pytest.raises(SeedConflict, match="seed_conflict"):
            seed_dataset(analytics_url, dataset)
        assert _counts(analytics_url) == expected_counts

        with psycopg.connect(analytics_url) as connection:
            connection.execute("DELETE FROM maintenance.dataset_seeds")
        with pytest.raises(SeedConflict, match="seed_conflict"):
            seed_dataset(analytics_url, dataset)
        assert _counts(analytics_url) == expected_counts
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database,),
            )
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


def _counts(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        return {
            table: connection.execute(
                sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in TABLE_LOAD_ORDER
        }
