from __future__ import annotations

import csv
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import Engines
from app.models import (
    Customer,
    Order,
    OrderItem,
    Product,
    ProductCategory,
    QueryRun,
)


ANALYTICS_TABLES = (
    ProductCategory.__table__,
    Customer.__table__,
    Product.__table__,
    Order.__table__,
    OrderItem.__table__,
)

SEED_FILES = (
    ("product_categories", ProductCategory.__table__),
    ("customers", Customer.__table__),
    ("products", Product.__table__),
    ("orders", Order.__table__),
    ("order_items", OrderItem.__table__),
)


def wait_for_databases(engines: Engines, attempts: int = 30, delay_seconds: float = 1.0) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with engines.platform.connect() as conn:
                conn.execute(text("SELECT 1"))
            with engines.analytics_owner.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay_seconds)
    raise RuntimeError("databases did not become reachable") from last_error


def run_migrations(api_dir: Path) -> None:
    platform_cfg = Config(str(api_dir / "alembic.platform.ini"))
    analytics_cfg = Config(str(api_dir / "alembic.analytics.ini"))
    command.upgrade(platform_cfg, "head")
    command.upgrade(analytics_cfg, "head")


def seed_analytics(engine: Engine, dataset_dir: Path) -> None:
    data_dir = dataset_dir / "data"
    with engine.begin() as conn:
        conn.execute(text("SET search_path TO analytics, public"))
        conn.execute(
            text(
                "TRUNCATE TABLE "
                "analytics.order_items, analytics.orders, analytics.products, "
                "analytics.customers, analytics.product_categories "
                "RESTART IDENTITY CASCADE"
            )
        )
        for name, table in SEED_FILES:
            path = data_dir / f"{name}.csv"
            with path.open(newline="", encoding="utf-8") as handle:
                rows = [_coerce_row(name, row) for row in csv.DictReader(handle)]
            if not rows:
                continue
            conn.execute(table.insert(), rows)
        conn.execute(
            text(
                "GRANT SELECT ON analytics.customers, analytics.product_categories, "
                "analytics.products, analytics.orders, analytics.order_items TO analytics_reader"
            )
        )


def _coerce_row(table_name: str, row: dict[str, str]) -> dict[str, object]:
    coerced: dict[str, object] = dict(row)
    int_fields = {
        "product_categories": ("id",),
        "customers": ("id",),
        "products": ("id", "category_id"),
        "orders": ("id", "customer_id"),
        "order_items": ("id", "order_id", "product_id", "quantity"),
    }
    for field in int_fields.get(table_name, ()):
        coerced[field] = int(row[field])
    if table_name == "products":
        coerced["active"] = row["active"].strip().lower() == "true"
    return coerced


def verify_ready(engines: Engines) -> None:
    with engines.platform.connect() as conn:
        conn.execute(text(f"SELECT 1 FROM {QueryRun.__tablename__} LIMIT 1"))
    with engines.analytics_reader.connect() as conn:
        conn.execute(text("SET search_path TO analytics"))
        for table in ANALYTICS_TABLES:
            conn.execute(text(f"SELECT 1 FROM {table.schema}.{table.name} LIMIT 1"))


def bootstrap(settings: Settings, engines: Engines, api_dir: Path) -> None:
    wait_for_databases(engines)
    run_migrations(api_dir)
    seed_analytics(engines.analytics_owner, Path(settings.dataset_dir))
    verify_ready(engines)
