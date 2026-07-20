"""Validate the committed sales analytics fixture."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONTRACT_PATH = ROOT / "contract.json"
TABLES = ["customers", "product_categories", "products", "orders", "order_items"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_csv(root: Path, table: str) -> list[dict[str, str]]:
    with (root / "data" / f"{table}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_fixture(root: Path, require_default_seed: bool = True) -> None:
    contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == contract["dataset"]
    assert manifest["version"] == contract["version"]
    assert manifest["contract_sha256"] == sha256_file(root / "contract.json")
    if require_default_seed:
        assert manifest["seed"] == contract["default_seed"], "public fixture must use the default seed"

    rows = {table: load_csv(root, table) for table in TABLES}
    expected_counts = contract["expected_counts"]
    for table in TABLES:
        assert len(rows[table]) == expected_counts[table], f"{table}: unexpected row count"
        file_info = manifest["files"][table]
        path = root / file_info["path"]
        assert file_info["rows"] == len(rows[table])
        assert file_info["sha256"] == sha256_file(path), f"{table}: hash mismatch"

    customers = {int(row["id"]): row for row in rows["customers"]}
    categories = {int(row["id"]): row for row in rows["product_categories"]}
    products = {int(row["id"]): row for row in rows["products"]}
    orders = {int(row["id"]): row for row in rows["orders"]}
    assert len(customers) == len(rows["customers"])
    assert len(categories) == len(rows["product_categories"])
    assert len(products) == len(rows["products"])
    assert len(orders) == len(rows["orders"])
    assert len({row["customer_code"] for row in rows["customers"]}) == 100
    assert len({row["category_code"] for row in rows["product_categories"]}) == 8
    assert len({row["sku"] for row in rows["products"]}) == 50
    assert len({row["order_no"] for row in rows["orders"]}) == 1000

    for row in rows["customers"]:
        assert row["region"] in {"Central", "East", "North", "South", "West"}
        assert row["segment"] in {"", "enterprise", "mid_market", "small_business"}
        parse_timestamp(row["created_at"])
    for row in rows["products"]:
        assert int(row["category_id"]) in categories
        assert Decimal(row["list_price"]) > 0
        assert Decimal(row["cost_price"]) > 0
        assert Decimal(row["cost_price"]) <= Decimal(row["list_price"])
        assert row["active"] in {"true", "false"}
    for row in rows["orders"]:
        assert int(row["customer_id"]) in customers
        ordered_at = parse_timestamp(row["ordered_at"])
        assert date(2024, 1, 1) <= ordered_at.date() <= date(2025, 12, 31)
        assert row["status"] in {"pending", "confirmed", "cancelled", "refunded"}
        assert row["currency"] == "CNY"

    status_counts = {}
    customer_order_counts = {customer_id: 0 for customer_id in customers}
    for row in rows["orders"]:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        customer_order_counts[int(row["customer_id"])] += 1
    assert status_counts == contract["order_status_counts"]
    assert {customer_id for customer_id, count in customer_order_counts.items() if count == 0} == set(range(96, 101))

    item_keys = set()
    product_order_counts = {product_id: 0 for product_id in products}
    for row in rows["order_items"]:
        order_id = int(row["order_id"])
        product_id = int(row["product_id"])
        key = (order_id, product_id)
        assert order_id in orders and product_id in products
        assert key not in item_keys, f"duplicate order/product pair: {key}"
        item_keys.add(key)
        product_order_counts[product_id] += 1
        assert int(row["quantity"]) > 0
        assert Decimal(row["unit_price"]) > 0
        discount = Decimal(row["discount_rate"])
        assert Decimal("0") <= discount <= Decimal("1")
    assert sum(count == 0 for count in product_order_counts.values()) >= 2
    assert any(products[product_id]["active"] == "false" and count > 0 for product_id, count in product_order_counts.items())
    assert len(item_keys) == 3000

    months = {(parse_timestamp(row["ordered_at"]).year, parse_timestamp(row["ordered_at"]).month) for row in rows["orders"]}
    assert len(months) == 24
    assert manifest["ordered_at"]["start"] == min(row["ordered_at"][:10] for row in rows["orders"])
    assert manifest["ordered_at"]["end"] == max(row["ordered_at"][:10] for row in rows["orders"])


def check_reproducibility() -> None:
    with TemporaryDirectory(prefix="sales-analytics-") as temp_dir:
        subprocess.run(
            [sys.executable, str(ROOT / "generate.py"), "--output", temp_dir],
            check=True,
            capture_output=True,
            text=True,
        )
        committed_manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        generated_manifest = json.loads((Path(temp_dir) / "manifest.json").read_text(encoding="utf-8"))
        assert committed_manifest["seed"] == generated_manifest["seed"]
        for table in TABLES:
            assert committed_manifest["files"][table]["sha256"] == generated_manifest["files"][table]["sha256"], table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="dataset directory to validate")
    parser.add_argument("--skip-reproducibility", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    check_fixture(root)
    if not args.skip_reproducibility and root == ROOT:
        check_reproducibility()
    print(f"validated {root}")


if __name__ == "__main__":
    main()
