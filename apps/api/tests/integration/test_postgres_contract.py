import json
import os
from pathlib import Path

import psycopg
import pytest

from decisionharbor.dataset import file_sha256


pytestmark = pytest.mark.integration


def psycopg_url(name: str) -> str:
    return os.environ[name].replace("postgresql+psycopg://", "postgresql://")


def test_api_identity_cannot_reach_the_analytics_database() -> None:
    platform_url = psycopg_url("PLATFORM_DATABASE_URL")
    with psycopg.connect(platform_url) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == "platform"
        assert connection.execute("SELECT count(*) FROM query_runs").fetchone()[0] >= 0

    for database in ("analytics", "postgres", "template1"):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(platform_url.rsplit("/", 1)[0] + f"/{database}")


def test_readiness_identity_is_bounded_to_migration_and_seed_markers() -> None:
    readiness_url = psycopg_url("ANALYTICS_READINESS_DATABASE_URL")
    with psycopg.connect(readiness_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW default_transaction_read_only")
            assert cursor.fetchone()[0] == "on"
            cursor.execute("SELECT version_num FROM public.alembic_version")
            assert cursor.fetchone()[0] == "analytics_0002"
            cursor.execute("SELECT count(*) FROM maintenance.dataset_seeds")
            assert cursor.fetchone()[0] == 1
        for statement in (
            "SELECT count(*) FROM analytics.customers",
            "CREATE TEMP TABLE forbidden_readiness_temp (id integer)",
        ):
            with pytest.raises(psycopg.Error):
                connection.execute(statement)


def test_seed_marker_matches_manifest_and_expected_counts() -> None:
    root = Path(os.environ["DATASET_ROOT"])
    contract = json.loads((root / "contract.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    with psycopg.connect(psycopg_url("ANALYTICS_READINESS_DATABASE_URL")) as connection:
        marker = connection.execute(
            """
            SELECT contract_sha256, manifest_sha256, row_counts
            FROM maintenance.dataset_seeds
            WHERE dataset = %s AND version = %s
            """,
            (manifest["dataset"], manifest["version"]),
        ).fetchone()
    assert marker[0].strip() == manifest["contract_sha256"]
    assert marker[1].strip() == file_sha256(root / "manifest.json")
    assert marker[2] == contract["expected_counts"]
