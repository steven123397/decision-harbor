"""幂等 seed：事务内 TRUNCATE 契约五表 + COPY 装载 CSV + 对照契约校验行数。

对应 docs/design/data-and-runtime.md：analytics 数据由 seed 独占，
截断重载保证任意次重复执行结果一致且无残留。
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from app.config import get_settings
from app.db import psycopg_url

TABLES_IN_LOAD_ORDER = [
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
]

_TRUNCATE = (
    "TRUNCATE analytics.order_items, analytics.orders, analytics.products, "
    "analytics.product_categories, analytics.customers"
)


def run() -> dict[str, int]:
    settings = get_settings()
    datasets = Path(settings.datasets_dir)
    contract = json.loads((datasets / "contract.json").read_text(encoding="utf-8"))
    expected = contract["expected_counts"]

    with psycopg.connect(psycopg_url(settings.analytics_owner_url)) as conn:
        with conn.transaction():
            conn.execute(_TRUNCATE)
            for table in TABLES_IN_LOAD_ORDER:
                csv_path = datasets / "data" / f"{table}.csv"
                with (
                    conn.cursor() as cur,
                    open(csv_path, encoding="utf-8") as handle,
                    cur.copy(
                        f"COPY analytics.{table} FROM STDIN WITH (FORMAT csv, HEADER true)"
                    ) as copy,
                ):
                    while chunk := handle.read(65536):
                        copy.write(chunk)
            counts = {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM analytics.{table}"
                ).fetchone()[0]
                for table in TABLES_IN_LOAD_ORDER
            }
            for table, count in counts.items():
                if count != expected[table]:
                    raise RuntimeError(
                        f"seed 校验失败：{table} 期望 {expected[table]} 行，实际 {count} 行。"
                    )
    return counts


if __name__ == "__main__":
    print(run())
