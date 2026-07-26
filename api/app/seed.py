"""幂等 seed：把权威 CSV 装载进 analytics 契约表。

策略（见 docs/design/local-runtime.md）：五表全空则装载；行数与契约
expected_counts 全部一致则跳过；其他情况报错退出，不静默清空重灌。
以 analytics_owner 身份运行，凭据仅此流程持有。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# 装载顺序满足外键依赖
TABLE_ORDER = ["customers", "product_categories", "products", "orders", "order_items"]
SCHEMA = "analytics"


def _dataset_dir() -> Path:
    return Path(os.environ.get("DATASET_DIR", "/data/sales-analytics-v1"))


def _load_contract(dataset_dir: Path) -> dict:
    return json.loads((dataset_dir / "contract.json").read_text(encoding="utf-8"))


def _check_csv_header(csv_path: Path, expected_columns: list[str]) -> None:
    with csv_path.open(encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    if header != expected_columns:
        raise SystemExit(
            f"seed 中止：{csv_path.name} 表头 {header} 与契约列 {expected_columns} 不一致"
        )


def main() -> int:
    dataset_dir = _dataset_dir()
    contract = _load_contract(dataset_dir)
    expected_counts: dict[str, int] = contract["expected_counts"]
    columns_by_table = {
        table["name"]: [column["name"] for column in table["columns"]]
        for table in contract["tables"]
    }

    engine = create_engine(os.environ["ANALYTICS_MIGRATION_URL"])
    with engine.connect() as conn:
        current_counts = {
            table: conn.execute(
                text(f"SELECT count(*) FROM {SCHEMA}.{table}")  # noqa: S608 固定表名
            ).scalar_one()
            for table in TABLE_ORDER
        }

    if all(current_counts[t] == expected_counts[t] for t in TABLE_ORDER):
        print("seed 跳过：各表行数与契约一致，数据已装载")
        return 0

    if any(current_counts[t] != 0 for t in TABLE_ORDER):
        print(
            "seed 中止：数据既非全空也与契约行数不一致，"
            f"当前行数 {current_counts}，期望 {expected_counts}。"
            "请检查原因后显式重置（删除数据卷重建）。",
            file=sys.stderr,
        )
        return 1

    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cursor:
            for table in TABLE_ORDER:
                csv_path = dataset_dir / "data" / f"{table}.csv"
                _check_csv_header(csv_path, columns_by_table[table])
                with csv_path.open(encoding="utf-8") as f, cursor.copy(
                    f"COPY {SCHEMA}.{table} FROM STDIN (FORMAT csv, HEADER true)"
                ) as copy:
                    while chunk := f.read(65536):
                        copy.write(chunk)

    with engine.connect() as conn:
        for table in TABLE_ORDER:
            loaded = conn.execute(
                text(f"SELECT count(*) FROM {SCHEMA}.{table}")  # noqa: S608 固定表名
            ).scalar_one()
            if loaded != expected_counts[table]:
                print(
                    f"seed 失败：{table} 装载后行数 {loaded} != 期望 {expected_counts[table]}",
                    file=sys.stderr,
                )
                return 1

    print(f"seed 完成：{ {t: expected_counts[t] for t in TABLE_ORDER} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
