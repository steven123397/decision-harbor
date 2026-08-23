import json
import os
from pathlib import Path

import psycopg
import pytest

from decisionharbor.dataset import file_sha256


pytestmark = pytest.mark.integration


def psycopg_url(name: str) -> str:
    return os.environ[name].replace("postgresql+psycopg://", "postgresql://")


def test_analytics_schema_and_rows_match_contract() -> None:
    contract = json.loads((Path(os.environ["DATASET_ROOT"]) / "contract.json").read_text())
    with psycopg.connect(psycopg_url("ANALYTICS_DATABASE_URL")) as connection:
        with connection.cursor() as cursor:
            for table in contract["tables"]:
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable, character_maximum_length,
                           numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = 'analytics' AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (table["name"],),
                )
                actual_columns = cursor.fetchall()
                assert len(actual_columns) == len(table["columns"])
                for actual, expected in zip(actual_columns, table["columns"], strict=True):
                    assert actual[0] == expected["name"]
                    assert actual[2] == ("YES" if expected["nullable"] else "NO")
                    expected_type = expected["type"]
                    if expected_type.startswith("varchar"):
                        assert (actual[1], actual[3]) == (
                            "character varying",
                            int(expected_type.removeprefix("varchar(").removesuffix(")")),
                        )
                    elif expected_type.startswith("char"):
                        assert (actual[1], actual[3]) == (
                            "character",
                            int(expected_type.removeprefix("char(").removesuffix(")")),
                        )
                    elif expected_type.startswith("numeric"):
                        precision, scale = expected_type.removeprefix("numeric(").removesuffix(")").split(",")
                        assert (actual[1], actual[4], actual[5]) == ("numeric", int(precision), int(scale))
                    else:
                        assert actual[1] == {
                            "bigint": "bigint",
                            "integer": "integer",
                            "boolean": "boolean",
                            "timestamptz": "timestamp with time zone",
                        }[expected_type]
                cursor.execute(f'SELECT count(*) FROM analytics."{table["name"]}"')
                assert cursor.fetchone()[0] == table["row_count"]

                cursor.execute(
                    """
                    SELECT attribute.attname
                    FROM pg_catalog.pg_constraint AS constraint_record
                    CROSS JOIN LATERAL unnest(constraint_record.conkey)
                      WITH ORDINALITY AS key_column(attnum, position)
                    JOIN pg_catalog.pg_attribute AS attribute
                      ON attribute.attrelid = constraint_record.conrelid
                     AND attribute.attnum = key_column.attnum
                    WHERE constraint_record.conrelid = to_regclass(%s)
                      AND constraint_record.contype = 'p'
                    ORDER BY key_column.position
                    """,
                    (f"analytics.{table['name']}",),
                )
                expected_primary_key = [
                    column["name"] for column in table["columns"] if column.get("primary_key")
                ]
                assert [row[0] for row in cursor.fetchall()] == expected_primary_key

                cursor.execute(
                    """
                    SELECT array_agg(attribute.attname ORDER BY key_column.position)
                    FROM pg_catalog.pg_constraint AS constraint_record
                    CROSS JOIN LATERAL unnest(constraint_record.conkey)
                      WITH ORDINALITY AS key_column(attnum, position)
                    JOIN pg_catalog.pg_attribute AS attribute
                      ON attribute.attrelid = constraint_record.conrelid
                     AND attribute.attnum = key_column.attnum
                    WHERE constraint_record.conrelid = to_regclass(%s)
                      AND constraint_record.contype = 'u'
                    GROUP BY constraint_record.oid
                    """,
                    (f"analytics.{table['name']}",),
                )
                actual_unique = {tuple(row[0]) for row in cursor.fetchall()}
                expected_unique = {
                    (column["name"],) for column in table["columns"] if column.get("unique")
                } | {tuple(columns) for columns in table.get("unique_constraints", [])}
                assert actual_unique == expected_unique

                cursor.execute(
                    """
                    SELECT source_attribute.attname, target_table.relname, target_attribute.attname
                    FROM pg_catalog.pg_constraint AS constraint_record
                    CROSS JOIN LATERAL generate_subscripts(constraint_record.conkey, 1)
                      AS key_position(position)
                    JOIN pg_catalog.pg_attribute AS source_attribute
                      ON source_attribute.attrelid = constraint_record.conrelid
                     AND source_attribute.attnum = constraint_record.conkey[key_position.position]
                    JOIN pg_catalog.pg_class AS target_table
                      ON target_table.oid = constraint_record.confrelid
                    JOIN pg_catalog.pg_attribute AS target_attribute
                      ON target_attribute.attrelid = constraint_record.confrelid
                     AND target_attribute.attnum = constraint_record.confkey[key_position.position]
                    WHERE constraint_record.conrelid = to_regclass(%s)
                      AND constraint_record.contype = 'f'
                    """,
                    (f"analytics.{table['name']}",),
                )
                actual_references = set(cursor.fetchall())
                expected_references = {
                    (reference["column"], reference["table"], reference["target_column"])
                    for reference in table["references"]
                }
                assert actual_references == expected_references


def test_runtime_database_identities_are_independently_bounded() -> None:
    analytics_url = psycopg_url("ANALYTICS_DATABASE_URL")
    readiness_url = psycopg_url("ANALYTICS_READINESS_DATABASE_URL")
    platform_url = psycopg_url("PLATFORM_DATABASE_URL")
    platform_worker_url = psycopg_url("PLATFORM_WORKER_DATABASE_URL")
    with psycopg.connect(analytics_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW default_transaction_read_only")
            assert cursor.fetchone()[0] == "on"
            cursor.execute("SELECT count(*) FROM analytics.customers")
            assert cursor.fetchone()[0] == 100

    for statement in (
        "SELECT * FROM maintenance.dataset_seeds",
        "SELECT * FROM public.alembic_version",
    ):
        with psycopg.connect(analytics_url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)

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

    with psycopg.connect(platform_url) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == "platform"
        assert connection.execute(
            "SELECT has_table_privilege(current_user, 'query_results', 'INSERT')"
        ).fetchone()[0] is False

    with psycopg.connect(platform_worker_url) as connection:
        privileges = connection.execute(
            """
            SELECT has_table_privilege(current_user, 'query_runs', 'INSERT'),
                   has_table_privilege(current_user, 'query_runs', 'UPDATE'),
                   has_table_privilege(current_user, 'query_results', 'INSERT')
            """
        ).fetchone()
        assert privileges == (False, True, True)

    for statement in (
        "INSERT INTO analytics.customers (id, customer_code, display_name, region, created_at) VALUES (9999, 'X', 'X', 'East', now())",
        "CREATE TABLE analytics.forbidden (id integer)",
        "CREATE TEMP TABLE forbidden_temp (id integer)",
    ):
        with psycopg.connect(analytics_url) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)

    for runtime_url, forbidden_databases in (
        (analytics_url, ("platform", "postgres", "template1")),
        (readiness_url, ("platform", "postgres", "template1")),
        (platform_url, ("analytics", "postgres", "template1")),
        (platform_worker_url, ("analytics", "postgres", "template1")),
    ):
        for database in forbidden_databases:
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(runtime_url.rsplit("/", 1)[0] + f"/{database}")


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
