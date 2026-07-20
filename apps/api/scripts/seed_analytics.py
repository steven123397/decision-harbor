"""Idempotent seed of analytics tables from sales-analytics-v1 CSV fixtures."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

TABLE_ORDER = [
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
]


def main() -> int:
    dataset_dir = Path(os.environ.get("DATASET_DIR", "/datasets/sales-analytics-v1"))
    url = os.environ["ANALYTICS_MIGRATOR_URL"]
    contract_path = dataset_dir / "contract.json"
    data_dir = dataset_dir / "data"

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    expected = contract["expected_counts"]
    dataset = contract["dataset"]
    version = contract["version"]

    engine = create_engine(url)
    with engine.begin() as conn:
        # Clear in FK-safe order via CASCADE from parents.
        conn.execute(text("TRUNCATE order_items, orders, products, product_categories, customers RESTART IDENTITY CASCADE"))
        conn.execute(text("TRUNCATE analytics_seed_meta RESTART IDENTITY"))

        for table in TABLE_ORDER:
            csv_path = data_dir / f"{table}.csv"
            with csv_path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                if not rows:
                    continue
                columns = list(rows[0].keys())
                col_list = ", ".join(columns)
                placeholders = ", ".join(f":{c}" for c in columns)
                stmt = text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})")
                # Normalize empty strings to None for nullable columns.
                payload = []
                for row in rows:
                    cleaned = {}
                    for k, v in row.items():
                        if v == "":
                            cleaned[k] = None
                        elif table == "products" and k == "active":
                            cleaned[k] = str(v).lower() in {"1", "true", "t", "yes"}
                        else:
                            cleaned[k] = v
                    payload.append(cleaned)
                conn.execute(stmt, payload)

        for table, count in expected.items():
            actual = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            if actual != count:
                raise SystemExit(f"seed count mismatch for {table}: {actual} != {count}")

        conn.execute(
            text(
                "INSERT INTO analytics_seed_meta (dataset, version) VALUES (:d, :v)"
            ),
            {"d": dataset, "v": version},
        )

    print(f"seeded {dataset}@{version} from {dataset_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
