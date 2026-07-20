import json

import psycopg
from psycopg import sql

from decisionharbor.dataset import DatasetContract, TABLE_LOAD_ORDER


class SeedConflict(RuntimeError):
    pass


def seed_dataset(database_url: str, dataset: DatasetContract) -> str:
    expected_counts = dataset.contract["expected_counts"]
    marker_key = (dataset.manifest["dataset"], dataset.manifest["version"])
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (marker_key[0],))
                actual_counts = _row_counts(cursor)
                cursor.execute(
                    """
                    SELECT contract_sha256, manifest_sha256, row_counts
                    FROM maintenance.dataset_seeds
                    WHERE dataset = %s AND version = %s
                    """,
                    marker_key,
                )
                marker = cursor.fetchone()
                if marker:
                    matches = (
                        marker[0].strip() == dataset.contract_sha256
                        and marker[1].strip() == dataset.manifest_sha256
                        and marker[2] == expected_counts
                        and actual_counts == expected_counts
                    )
                    if not matches:
                        raise SeedConflict("seed_conflict: existing seed marker or rows do not match")
                    return "unchanged"
                if any(actual_counts.values()):
                    raise SeedConflict("seed_conflict: analytics tables contain unmarked rows")

                columns_by_table = {
                    table["name"]: [column["name"] for column in table["columns"]]
                    for table in dataset.contract["tables"]
                }
                for table in TABLE_LOAD_ORDER:
                    info = dataset.manifest["files"][table]
                    copy_statement = sql.SQL(
                        "COPY analytics.{} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)"
                    ).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(map(sql.Identifier, columns_by_table[table])),
                    )
                    with cursor.copy(copy_statement) as copy:
                        with (dataset.root / info["path"]).open("r", encoding="utf-8", newline="") as stream:
                            while chunk := stream.read(1024 * 1024):
                                copy.write(chunk)

                _verify_loaded_fixture(cursor, dataset)
                cursor.execute(
                    """
                    INSERT INTO maintenance.dataset_seeds
                        (dataset, version, contract_sha256, manifest_sha256, row_counts)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        *marker_key,
                        dataset.contract_sha256,
                        dataset.manifest_sha256,
                        json.dumps(expected_counts, sort_keys=True),
                    ),
                )
    return "seeded"


def _row_counts(cursor: psycopg.Cursor) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLE_LOAD_ORDER:
        cursor.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table)))
        counts[table] = cursor.fetchone()[0]
    return counts


def _verify_loaded_fixture(cursor: psycopg.Cursor, dataset: DatasetContract) -> None:
    if _row_counts(cursor) != dataset.contract["expected_counts"]:
        raise SeedConflict("seed_conflict: loaded row counts do not match contract")
    cursor.execute("SELECT status, count(*) FROM analytics.orders GROUP BY status")
    if dict(cursor.fetchall()) != dataset.contract["order_status_counts"]:
        raise SeedConflict("seed_conflict: order status distribution does not match contract")
    cursor.execute("SELECT min(ordered_at)::date::text, max(ordered_at)::date::text FROM analytics.orders")
    observed_range = cursor.fetchone()
    expected_range = dataset.contract["date_range"]
    if observed_range != (expected_range["ordered_at_start"], expected_range["ordered_at_end"]):
        raise SeedConflict("seed_conflict: order date range does not match contract")
