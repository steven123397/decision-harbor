"""Generate the deterministic public sales analytics fixture."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


MASK = (1 << 64) - 1
ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "contract.json"
DEFAULT_SEED = 20260720
DATE_START = date(2024, 1, 1)
DATE_END = date(2025, 12, 31)
MONEY_QUANTUM = Decimal("0.01")


class StableRng:
    """Small version-independent PRNG for reproducible fixture generation."""

    def __init__(self, seed: int) -> None:
        self.state = (seed & MASK) or 0x9E3779B97F4A7C15

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
        return (value ^ (value >> 31)) & MASK

    def below(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper must be positive")
        return self.next_u64() % upper

    def choose(self, values):
        return values[self.below(len(values))]

    def shuffle(self, values: list) -> None:
        for index in range(len(values) - 1, 0, -1):
            other = self.below(index + 1)
            values[index], values[other] = values[other], values[index]

    def sample(self, values: list, count: int) -> list:
        if count > len(values):
            raise ValueError("sample larger than population")
        result = list(values)
        self.shuffle(result)
        return result[:count]


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def rate(basis_points: int) -> str:
    return f"{Decimal(basis_points) / 10000:.4f}"


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(seed: int) -> dict[str, list[dict]]:
    rng = StableRng(seed)
    regions = ["North", "East", "South", "West", "Central"]
    segments = ["enterprise", "mid_market", "small_business"]
    categories = [
        (1, "ELEC", "Electronics", 160000),
        (2, "OFFICE", "Office Supplies", 4000),
        (3, "FURN", "Furniture", 70000),
        (4, "INDUST", "Industrial Equipment", 250000),
        (5, "NETWORK", "Networking", 90000),
        (6, "STORAGE", "Storage", 50000),
        (7, "SAFETY", "Safety", 12000),
        (8, "ACCESS", "Accessories", 8000),
    ]

    customers = []
    name_prefixes = ["Aurora", "Summit", "Harbor", "Pioneer", "Cedar", "Atlas", "Vertex", "Mosaic"]
    name_suffixes = ["Trading", "Works", "Retail", "Systems", "Logistics", "Services"]
    for customer_id in range(1, 101):
        created = datetime.combine(
            date(2022, 1, 1) + timedelta(days=rng.below(730)),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        segment = None if customer_id % 17 == 0 else segments[(customer_id - 1) % len(segments)]
        customers.append(
            {
                "id": customer_id,
                "customer_code": f"CUST-{customer_id:04d}",
                "display_name": f"{name_prefixes[(customer_id - 1) % len(name_prefixes)]} {name_suffixes[(customer_id - 1) % len(name_suffixes)]} {customer_id:03d}",
                "region": regions[(customer_id * 3) % len(regions)],
                "segment": segment or "",
                "created_at": utc_text(created),
            }
        )

    product_categories = [
        {"id": category_id, "category_code": code, "name": name}
        for category_id, code, name, _ in categories
    ]

    products = []
    for product_id in range(1, 51):
        category_id, category_code, category_name, base_cents = categories[(product_id - 1) % len(categories)]
        sequence = (product_id - 1) // len(categories) + 1
        factor = 75 + rng.below(126)
        list_cents = max(100, base_cents * factor // 100)
        margin_percent = 50 + rng.below(26)
        cost_cents = max(1, list_cents * margin_percent // 100)
        products.append(
            {
                "id": product_id,
                "sku": f"SKU-{category_code}-{sequence:02d}",
                "name": f"{category_name} Model {sequence:02d}",
                "category_id": category_id,
                "list_price": money(list_cents),
                "cost_price": money(cost_cents),
                "active": "true" if product_id <= 45 else "false",
            }
        )

    statuses = ["confirmed"] * 720 + ["pending"] * 100 + ["cancelled"] * 100 + ["refunded"] * 80
    rng.shuffle(statuses)
    orders = []
    for order_id in range(1, 1001):
        day_offset = (order_id - 1) * (DATE_END - DATE_START).days // 999
        ordered_date = DATE_START + timedelta(days=day_offset)
        ordered_at = datetime.combine(
            ordered_date,
            datetime.min.time().replace(hour=8 + rng.below(12), minute=rng.below(60)),
            tzinfo=timezone.utc,
        )
        customer_id = order_id if order_id <= 95 else 1 + rng.below(95)
        orders.append(
            {
                "id": order_id,
                "order_no": f"SO-{ordered_date.year}-{order_id:06d}",
                "customer_id": customer_id,
                "ordered_at": utc_text(ordered_at),
                "status": statuses[order_id - 1],
                "currency": "CNY",
            }
        )

    item_counts = [count for count in [1, 2, 3, 4, 5] for _ in range(200)]
    rng.shuffle(item_counts)
    order_items = []
    item_id = 1
    product_ids = list(range(1, 49))
    price_factors = [9000, 9500, 10000, 10500, 11000]
    discount_rates = [0, 0, 0, 500, 1000, 1500, 2000, 2500, 3000]
    quantities = [1, 1, 1, 2, 2, 3, 4, 5, 10, 20]
    product_by_id = {product["id"]: product for product in products}
    for order_id, item_count in enumerate(item_counts, start=1):
        selected_products = rng.sample(product_ids, item_count)
        if order_id <= 3 and 45 + order_id not in selected_products:
            selected_products[0] = 45 + order_id
        for product_id in selected_products:
            product = product_by_id[product_id]
            list_cents = int(Decimal(product["list_price"]) * 100)
            unit_cents = (list_cents * rng.choose(price_factors) + 5000) // 10000
            order_items.append(
                {
                    "id": item_id,
                    "order_id": order_id,
                    "product_id": product_id,
                    "quantity": rng.choose(quantities),
                    "unit_price": money(unit_cents),
                    "discount_rate": rate(rng.choose(discount_rates)),
                }
            )
            item_id += 1

    return {
        "customers": customers,
        "product_categories": product_categories,
        "products": products,
        "orders": orders,
        "order_items": order_items,
    }


FIELDNAMES = {
    "customers": ["id", "customer_code", "display_name", "region", "segment", "created_at"],
    "product_categories": ["id", "category_code", "name"],
    "products": ["id", "sku", "name", "category_id", "list_price", "cost_price", "active"],
    "orders": ["id", "order_no", "customer_id", "ordered_at", "status", "currency"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price", "discount_rate"],
}


def create_manifest(output: Path, seed: int, rows: dict[str, list[dict]]) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    files = {}
    for table_name in FIELDNAMES:
        path = output / "data" / f"{table_name}.csv"
        files[table_name] = {"path": f"data/{table_name}.csv", "rows": len(rows[table_name]), "sha256": sha256_file(path)}
    status_counts = {}
    for row in rows["orders"]:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    manifest = {
        "dataset": contract["dataset"],
        "version": contract["version"],
        "seed": seed,
        "contract_sha256": sha256_file(CONTRACT_PATH),
        "generator_sha256": sha256_file(Path(__file__).resolve()),
        "files": files,
        "status_counts": status_counts,
        "ordered_at": {"start": rows["orders"][0]["ordered_at"][:10], "end": rows["orders"][-1]["ordered_at"][:10]},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def generate(output: Path, seed: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    data_dir = output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rows = build_dataset(seed)
    for table_name, fieldnames in FIELDNAMES.items():
        write_csv(data_dir / f"{table_name}.csv", fieldnames, rows[table_name])
    create_manifest(output, seed, rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT, help="dataset output directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic generation seed")
    args = parser.parse_args()
    generate(args.output.resolve(), args.seed)
    print(f"generated {args.output.resolve()}")


if __name__ == "__main__":
    main()
