"""幂等 seed：将固定销售分析数据建立到 analytics 库。

严格遵循 datasets/sales-analytics-v1/contract.json 的字段与业务口径；
不改写契约。重复运行收敛到契约的规范状态，不累积重复行。
"""
import json
import pathlib

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings

# 按外键依赖顺序加载（父表在前）
LOAD_ORDER = ["product_categories", "customers", "products", "orders", "order_items"]


def _build_create(table: dict) -> str:
    parts: list[str] = []
    for c in table["columns"]:
        line = f'"{c["name"]}" {c["type"]}'
        if not c.get("nullable", True):
            line += " NOT NULL"
        if c.get("primary_key"):
            line += " PRIMARY KEY"
        if c.get("unique"):
            line += " UNIQUE"
        parts.append(line)
    for r in table.get("references", []):
        parts.append(
            f'FOREIGN KEY ("{r["column"]}") REFERENCES analytics."{r["table"]}" ("{r["target_column"]}")'
        )
    for uc in table.get("unique_constraints", []):
        cols = ", ".join(f'"{x}"' for x in uc)
        parts.append(f"UNIQUE ({cols})")
    body = ",\n  ".join(parts)
    return f'CREATE TABLE IF NOT EXISTS analytics."{table["name"]}" (\n  {body}\n)'


def _engine() -> Engine:
    s = get_settings()
    dsn = (
        f"postgresql+psycopg://{s.bootstrap_user}:{s.bootstrap_password}"
        f"@{s.db_host}:{s.db_port}/{s.analytics_db}"
    )
    return create_engine(dsn, pool_pre_ping=True, future=True)


def run() -> None:
    s = get_settings()
    base = pathlib.Path(s.analytics_seed_dir)
    contract = json.loads((base / "contract.json").read_text(encoding="utf-8"))
    tables_by_name = {t["name"]: t for t in contract["tables"]}
    order = [n for n in LOAD_ORDER if n in tables_by_name]

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
        for name in order:
            conn.execute(text(_build_create(tables_by_name[name])))

        all_tables = ", ".join(f'analytics."{n}"' for n in order)
        conn.execute(text(f"TRUNCATE {all_tables} RESTART IDENTITY CASCADE"))

        for name in order:
            path = base / "data" / f"{name}.csv"
            raw = conn.connection
            with raw.cursor() as cur, open(path, "rb") as f:
                with cur.copy(
                    f'COPY analytics."{name}" FROM STDIN WITH (FORMAT csv, HEADER true)'
                ) as cp:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        cp.write(chunk)

        # 校验行数与契约一致
        for name in order:
            expected = contract["expected_counts"][name]
            actual = conn.execute(
                text(f'SELECT count(*) FROM analytics."{name}"')
            ).scalar()
            if actual != expected:
                raise RuntimeError(
                    f"seed row count mismatch for {name}: {actual} != {expected}"
                )

        reader = s.analytics_reader_user
        conn.execute(text(f"REVOKE CREATE ON SCHEMA analytics FROM PUBLIC"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA analytics TO {reader}"))
        conn.execute(
            text(f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {reader}")
        )
        conn.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA analytics "
                f"GRANT SELECT ON TABLES TO {reader}"
            )
        )

    engine.dispose()


if __name__ == "__main__":
    run()
