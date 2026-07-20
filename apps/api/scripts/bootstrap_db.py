"""Create logical databases and roles (idempotent). Uses admin connection."""

from __future__ import annotations

import os
import sys

import psycopg


ADMIN_URL = os.environ.get(
    "POSTGRES_ADMIN_URL",
    "postgresql://postgres:postgres@db:5432/postgres",
)

ROLE_PASSWORDS = {
    "platform_app": os.environ.get("PLATFORM_APP_PASSWORD", "platform_app"),
    "analytics_migrator": os.environ.get(
        "ANALYTICS_MIGRATOR_PASSWORD", "analytics_migrator"
    ),
    "analytics_readonly": os.environ.get(
        "ANALYTICS_READONLY_PASSWORD", "analytics_readonly"
    ),
}


def _exec(cur: psycopg.Cursor, sql: str) -> None:
    cur.execute(sql)  # type: ignore[arg-type]


def ensure_role(cur: psycopg.Cursor, role: str, password: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cur.fetchone() is None:
        _exec(cur, f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
    else:
        _exec(cur, f"ALTER ROLE {role} WITH LOGIN PASSWORD '{password}'")


def ensure_database(cur: psycopg.Cursor, name: str, owner: str) -> None:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{name}" OWNER {owner}')


def main() -> int:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            for role, password in ROLE_PASSWORDS.items():
                ensure_role(cur, role, password)

            ensure_database(cur, "platform", "platform_app")
            ensure_database(cur, "analytics", "analytics_migrator")

            # Platform grants
            cur.execute("GRANT ALL PRIVILEGES ON DATABASE platform TO platform_app")
            # Analytics grants at DB level
            cur.execute("GRANT CONNECT ON DATABASE analytics TO analytics_migrator")
            cur.execute("GRANT CONNECT ON DATABASE analytics TO analytics_readonly")
            cur.execute("GRANT CONNECT ON DATABASE analytics TO platform_app")  # not used for SQL; harmless deny later

    # Schema privileges inside each DB
    platform_url = ADMIN_URL.rsplit("/", 1)[0] + "/platform"
    analytics_url = ADMIN_URL.rsplit("/", 1)[0] + "/analytics"

    with psycopg.connect(platform_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            _exec(cur, "GRANT ALL ON SCHEMA public TO platform_app")
            _exec(cur, "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO platform_app")
            _exec(cur, "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO platform_app")

    with psycopg.connect(analytics_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            _exec(cur, "GRANT ALL ON SCHEMA public TO analytics_migrator")
            _exec(cur, "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics_migrator")
            _exec(cur, "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics_migrator")
            # Readonly: only SELECT on business tables (applied after migrate/seed too)
            _exec(cur, "GRANT USAGE ON SCHEMA public TO analytics_readonly")
            for table in (
                "customers",
                "product_categories",
                "products",
                "orders",
                "order_items",
                "analytics_seed_meta",
            ):
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = %s
                    """,
                    (table,),
                )
                if cur.fetchone():
                    _exec(cur, f"GRANT SELECT ON TABLE {table} TO analytics_readonly")

            # Revoke write defaults from public if any
            _exec(cur, "REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    print("bootstrap_db complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
