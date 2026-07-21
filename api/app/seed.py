"""Idempotent seed: TRUNCATE + COPY from CSV into analytics database."""
import csv
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import settings

DATASET_DIR = Path(os.environ.get(
    "DATASET_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "datasets" / "sales-analytics-v1"),
))

TABLES_IN_ORDER = [
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
]


def seed():
    engine = create_engine(settings.analytics_admin_database_url)
    with engine.begin() as conn:
        for table in reversed(TABLES_IN_ORDER):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

        for table in TABLES_IN_ORDER:
            csv_path = DATASET_DIR / "data" / f"{table}.csv"
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                columns = ", ".join(header)
                placeholders = ", ".join([f":{col}" for col in header])
                insert_sql = text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})")
                rows = [dict(zip(header, row)) for row in reader]
                conn.execute(insert_sql, rows)

        for table in TABLES_IN_ORDER:
            conn.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 0) + 1, false) FROM {table}"
            ))

    print("Seed complete.")


if __name__ == "__main__":
    seed()
