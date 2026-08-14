"""Bootstrap: roles, databases, platform migrations, and idempotent analytics seed.

Run once (idempotently) before the API starts, and on demand for tests.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, MetaData, Table, create_engine, text

from .config import settings
from .schema_catalog import load_catalog


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _admin_engine(dsn: str):
    return create_engine(dsn, isolation_level="AUTOCOMMIT", future=True)


def ensure_roles_and_databases() -> None:
    engine = _admin_engine(settings.superuser_dsn)
    try:
        with engine.connect() as conn:
            roles = [
                (settings.dh_admin_user, settings.dh_admin_password),
                (settings.platform_writer_user, settings.platform_writer_password),
                (settings.analytics_reader_user, settings.analytics_reader_password),
            ]
            for role, password in roles:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role}
                ).first()
                if exists:
                    conn.execute(text(f"ALTER ROLE {_ident(role)} WITH LOGIN PASSWORD {_lit(password)}"))
                else:
                    conn.execute(text(f"CREATE ROLE {_ident(role)} LOGIN PASSWORD {_lit(password)}"))

            reader = _ident(settings.analytics_reader_user)
            conn.execute(text(f"ALTER ROLE {reader} NOSUPERUSER NOCREATEDB NOCREATEROLE"))
            conn.execute(text(f"ALTER ROLE {reader} SET default_transaction_read_only = on"))
            conn.execute(text(f"ALTER ROLE {reader} SET search_path = analytics"))

            for dbname in (settings.platform_db, settings.analytics_db):
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
                ).first()
                if not exists:
                    conn.execute(
                        text(f"CREATE DATABASE {_ident(dbname)} OWNER {_ident(settings.dh_admin_user)}")
                    )
    finally:
        engine.dispose()


def migrate_platform() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


def grant_platform() -> None:
    engine = _admin_engine(settings.platform_admin_dsn)
    writer = _ident(settings.platform_writer_user)
    try:
        with engine.connect() as conn:
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {writer}"))
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON query_runs TO {writer}"))
            conn.execute(text(f"GRANT SELECT ON dataset_seed TO {writer}"))
    finally:
        engine.dispose()


def _coerce_row(table, row: dict[str, str]) -> dict:
    out: dict = {}
    for col in table.columns:
        value = row[col.name]
        if value == "" and col.nullable:
            out[col.name] = None
        elif col.pg_type == "BIGINT":
            out[col.name] = int(value)
        elif col.pg_type == "INTEGER":
            out[col.name] = int(value)
        elif col.pg_type == "BOOLEAN":
            out[col.name] = value == "true"
        elif col.pg_type == "TIMESTAMPTZ":
            out[col.name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif col.pg_type.startswith("NUMERIC"):
            out[col.name] = Decimal(value)
        else:
            out[col.name] = value
    return out


def _load_csv(conn, catalog, name: str) -> None:
    table = catalog.tables[name]
    path = Path(settings.dataset_dir) / "data" / f"{name}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        raw_rows = list(csv.DictReader(stream))
    rows = [_coerce_row(table, row) for row in raw_rows]
    if rows:
        target = Table(name, MetaData(), *[Column(col.name) for col in table.columns], schema=catalog.schema)
        conn.execute(target.insert(), rows)


def seed_analytics() -> None:
    catalog = load_catalog(Path(settings.dataset_dir) / "contract.json")
    engine = _admin_engine(settings.analytics_admin_dsn)
    schema = _ident(catalog.schema)
    reader = _ident(settings.analytics_reader_user)
    try:
        with engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            for name in catalog.load_order():
                conn.execute(text(catalog.create_table_sql(name)))
            conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {reader}"))
            conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {reader}"))
            conn.execute(
                text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {reader}")
            )
            all_tables = ", ".join(f"{schema}.{_ident(n)}" for n in catalog.load_order())
            conn.execute(text(f"TRUNCATE TABLE {all_tables} RESTART IDENTITY CASCADE"))
            for name in catalog.load_order():
                _load_csv(conn, catalog, name)
    finally:
        engine.dispose()


def record_seed() -> None:
    contract_path = Path(settings.dataset_dir) / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    sha = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    engine = _admin_engine(settings.platform_admin_dsn)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dataset_seed (dataset, version, contract_sha256, seeded_at)
                    VALUES (:dataset, :version, :sha, now())
                    ON CONFLICT (dataset) DO UPDATE SET
                        version = EXCLUDED.version,
                        contract_sha256 = EXCLUDED.contract_sha256,
                        seeded_at = now()
                    """
                ),
                {"dataset": contract["dataset"], "version": contract["version"], "sha": sha},
            )
    finally:
        engine.dispose()


def bootstrap() -> None:
    ensure_roles_and_databases()
    migrate_platform()
    grant_platform()
    seed_analytics()
    record_seed()


if __name__ == "__main__":
    bootstrap()
    print("bootstrap complete")
