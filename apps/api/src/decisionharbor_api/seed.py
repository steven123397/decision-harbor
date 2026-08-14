from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg


TABLES = {
    "customers": ["id", "customer_code", "display_name", "region", "segment", "created_at"],
    "product_categories": ["id", "category_code", "name"],
    "products": ["id", "sku", "name", "category_id", "list_price", "cost_price", "active"],
    "orders": ["id", "order_no", "customer_id", "ordered_at", "status", "currency"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price", "discount_rate"],
}
INTEGER_FIELDS = {"id", "category_id", "customer_id", "order_id", "product_id", "quantity"}
DECIMAL_FIELDS = {"list_price", "cost_price", "unit_price", "discount_rate"}
TIMESTAMP_FIELDS = {"created_at", "ordered_at"}
BOOLEAN_FIELDS = {"active"}


class SeedConflict(RuntimeError):
    pass


def dataset_root() -> Path:
    configured = os.environ.get("DATASET_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[4] / "datasets" / "sales-analytics-v1"


def seed_dataset(database_url: str, root: Path | None = None) -> str:
    root = (root or dataset_root()).resolve()
    manifest = _load_manifest(root)
    rows = {table: _load_rows(root, table) for table in TABLES}
    expected_counts = {table: int(manifest["files"][table]["rows"]) for table in TABLES}

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        with connection.transaction():
            counts = _counts(connection)
            if all(value == 0 for value in counts.values()):
                _insert_rows(connection, rows)
                if _counts(connection) != expected_counts:
                    raise SeedConflict("Seed row counts do not match manifest")
                return "loaded"
            if counts == expected_counts and _database_matches(connection, rows):
                return "already_loaded"
            raise SeedConflict("Analytics database contains a partial or conflicting fixture")


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    contract_path = root / "contract.json"
    if manifest["contract_sha256"] != _sha256(contract_path):
        raise SeedConflict("Dataset contract hash does not match manifest")
    for table, info in manifest["files"].items():
        path = root / info["path"]
        if int(info["rows"]) != len(_load_rows(root, table)) or info["sha256"] != _sha256(path):
            raise SeedConflict(f"Dataset fixture hash or row count mismatch: {table}")
    return manifest


def _load_rows(root: Path, table: str) -> list[dict[str, str]]:
    with (root / "data" / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _counts(connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT count(*) FROM analytics.{table}").fetchone()[0])
        for table in TABLES
    }


def _insert_rows(connection, rows: dict[str, list[dict[str, str]]]) -> None:
    for table, fields in TABLES.items():
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        statement = f"INSERT INTO analytics.{table} ({columns}) VALUES ({placeholders})"
        values = [tuple(_convert(field, row[field]) for field in fields) for row in rows[table]]
        with connection.cursor() as cursor:
            cursor.executemany(statement, values)


def _database_matches(connection, rows: dict[str, list[dict[str, str]]]) -> bool:
    for table, fields in TABLES.items():
        selected = ", ".join(fields)
        actual = connection.execute(f"SELECT {selected} FROM analytics.{table} ORDER BY id").fetchall()
        expected = [tuple(_canonical(field, _convert(field, row[field])) for field in fields) for row in rows[table]]
        if [tuple(_canonical(field, value) for field, value in zip(fields, row)) for row in actual] != expected:
            return False
    return True


def _convert(field: str, value: str) -> Any:
    if field in INTEGER_FIELDS:
        return int(value)
    if field in DECIMAL_FIELDS:
        return Decimal(value)
    if field in TIMESTAMP_FIELDS:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if field in BOOLEAN_FIELDS:
        return value.lower() == "true"
    if field == "segment" and value == "":
        return None
    return value


def _canonical(field: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
