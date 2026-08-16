"""固定数据 seed：从契约派生 DDL，COPY 加载权威 CSV，幂等标记存 platform。

流程语义见 docs/design/data-and-seeding.md：
标记匹配且行数符合 → 跳过；表不存在 → 建表并单事务加载；其他不一致 → 失败。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql as pg_sql
from sqlalchemy import text
from sqlalchemy.engine import Engine

SCHEMA = "analytics"


class SeedError(RuntimeError):
    """seed 状态不一致，启动应失败并提示人工介入。"""


@dataclass(frozen=True)
class DatasetFacts:
    dataset: str
    version: str
    seed: int
    marker_key: str  # 形如 "sales-analytics/1.0.0/20260720"
    tables: tuple[str, ...]
    expected_counts: dict[str, int]


def load_facts(dataset_dir: Path) -> DatasetFacts:
    contract = json.loads((dataset_dir / "contract.json").read_text())
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    marker = f"{contract['dataset']}/{contract['version']}/{manifest['seed']}"
    tables = tuple(t["name"] for t in contract["tables"])
    return DatasetFacts(
        dataset=contract["dataset"],
        version=contract["version"],
        seed=manifest["seed"],
        marker_key=marker,
        tables=tables,
        expected_counts=dict(contract["expected_counts"]),
    )


def contract_tables(dataset_dir: Path) -> frozenset[str]:
    return frozenset(load_facts(dataset_dir).tables)


# ---- DDL 从契约派生 ------------------------------------------------------


def _render_type(raw: str) -> str:
    return raw.upper() if raw.lower().startswith("numeric") else raw.upper()


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _check_constraints(table: dict, rules: dict) -> list[str]:
    """由契约列约束与业务规则生成 CHECK 约束（与契约单一事实源派生）。

    - 列级 `allowed_values` → IN 清单；
    - business_rules.discount_rate_range → 对含 discount_rate 列的表生效；
    - business_rules.product_cost_not_above_list_price → 对同时含
      cost_price 与 list_price 列的表生效。
    """
    name = table["name"]
    checks: list[str] = []
    for col in table["columns"]:
        values = col.get("allowed_values")
        if values:
            literal = ", ".join(_quote_literal(v) for v in values)
            checks.append(
                f'CONSTRAINT "ck_{name}_{col["name"]}_allowed" '
                f'CHECK ("{col["name"]}" IN ({literal}))'
            )

    columns = {col["name"] for col in table["columns"]}
    rate_range = rules.get("discount_rate_range")
    if rate_range and "discount_rate" in columns:
        lo, hi = float(rate_range[0]), float(rate_range[1])
        checks.append(
            f'CONSTRAINT "ck_{name}_discount_rate_range" '
            f'CHECK ("discount_rate" BETWEEN {lo!r} AND {hi!r})'
        )

    if rules.get("product_cost_not_above_list_price") and {
        "cost_price",
        "list_price",
    } <= columns:
        checks.append(
            f'CONSTRAINT "ck_{name}_cost_not_above_list" '
            f'CHECK ("cost_price" <= "list_price")'
        )
    return checks


def build_create_table(table: dict, rules: dict | None = None) -> str:
    """由契约条目生成 CREATE TABLE IF NOT EXISTS，字段语义与契约一致。"""
    rules = rules or {}
    cols = []
    for col in table["columns"]:
        parts = [f'"{col["name"]}"', _render_type(col["type"])]
        if not col.get("nullable", True):
            parts.append("NOT NULL")
        cols.append(" ".join(parts))
    pk = [c["name"] for c in table["columns"] if c.get("primary_key")]
    if pk:
        cols.append("PRIMARY KEY (" + ", ".join(f'"{n}"' for n in pk) + ")")
    for col in table["columns"]:
        if col.get("unique"):
            cols.append(f'UNIQUE ("{col["name"]}")')
    for ref in table.get("references", []):
        cols.append(
            f'FOREIGN KEY ("{ref["column"]}") REFERENCES "{SCHEMA}"."{ref["table"]}" '
            f'("{ref["target_column"]}")'
        )
    for uq in table.get("unique_constraints", []):
        cols.append("UNIQUE (" + ", ".join(f'"{n}"' for n in uq) + ")")
    cols.extend(_check_constraints(table, rules))
    body = ",\n  ".join(cols)
    return f'CREATE TABLE IF NOT EXISTS "{SCHEMA}"."{table["name"]}" (\n  {body}\n)'


# ---- seed 主流程 ----------------------------------------------------------


def ensure_dataset(
    dataset_dir: Path,
    analytics_owner_dsn: str,
    platform_engine: Engine,
) -> str:
    """确保 analytics 固定数据就绪；返回 "seeded" 或 "skipped"。"""
    facts = load_facts(dataset_dir)
    contract = json.loads((dataset_dir / "contract.json").read_text())

    exists = _analytics_table_exists(analytics_owner_dsn)
    marker = _read_marker(platform_engine)

    if exists and marker == facts.marker_key and _counts_match(
        analytics_owner_dsn, facts
    ):
        return "skipped"

    if exists or (marker is not None and marker != facts.marker_key):
        raise SeedError(
            f"数据集状态不一致：表存在={exists}，标记={marker}，期望={facts.marker_key}；"
            "不自动重载，请人工确认后清理数据卷（docker compose down -v）"
        )

    _create_and_load(analytics_owner_dsn, contract, dataset_dir, facts)
    _write_marker(platform_engine, facts.marker_key)
    return "seeded"


def _analytics_table_exists(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = 'customers')",
            (SCHEMA,),
        )
        return cur.fetchone()[0]


def _counts_match(dsn: str, facts: DatasetFacts) -> bool:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table, expected in facts.expected_counts.items():
            cur.execute(pg_sql.SQL("SELECT count(*) FROM {}.{}").format(
                pg_sql.Identifier(SCHEMA), pg_sql.Identifier(table)
            ))
            if cur.fetchone()[0] != expected:
                return False
    return True


def _create_and_load(
    dsn: str, contract: dict, dataset_dir: Path, facts: DatasetFacts
) -> None:
    table_specs = {t["name"]: t for t in contract["tables"]}
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            cur = conn.cursor()
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
            rules = contract.get("business_rules", {})
            for name in facts.tables:  # 契约顺序即外键依赖顺序
                cur.execute(build_create_table(table_specs[name], rules))
            for name in facts.tables:
                csv_path = dataset_dir / "data" / f"{name}.csv"
                copy_sql = (
                    f'COPY "{SCHEMA}"."{name}" '
                    f'({", ".join(_columns(table_specs[name]))}) '
                    "FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                )
                with csv_path.open("rb") as f:
                    with cur.copy(copy_sql) as cp:
                        while chunk := f.read(65536):
                            cp.write(chunk)
            _grant_select(cur, facts.tables)


def _columns(table: dict) -> list[str]:
    return [f'"{c["name"]}"' for c in table["columns"]]


def _grant_select(cur, tables: tuple[str, ...]) -> None:
    """按契约表清单授权只读角色，表清单不在此处硬编码。"""
    cur.execute(pg_sql.SQL("GRANT USAGE ON SCHEMA {} TO analytics_readonly").format(
        pg_sql.Identifier(SCHEMA)
    ))
    for name in tables:
        cur.execute(
            pg_sql.SQL("GRANT SELECT ON {}.{} TO analytics_readonly").format(
                pg_sql.Identifier(SCHEMA), pg_sql.Identifier(name)
            )
        )


def _read_marker(platform_engine: Engine) -> str | None:
    with platform_engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'dataset_markers')"
            )
        ).scalar()
        if not exists:
            return None
        return conn.execute(
            text("SELECT marker FROM dataset_markers WHERE id = 1")
        ).scalar()


def _write_marker(platform_engine: Engine, marker: str) -> None:
    with platform_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dataset_markers (id, marker, updated_at) "
                "VALUES (1, :marker, now()) "
                "ON CONFLICT (id) DO UPDATE SET marker = EXCLUDED.marker, "
                "updated_at = now()"
            ),
            {"marker": marker},
        )
