"""Run platform + analytics migrations, seed, and refresh readonly grants."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT, env=env)


def grant_readonly() -> None:
    admin = os.environ.get(
        "POSTGRES_ADMIN_URL",
        "postgresql://postgres:postgres@db:5432/postgres",
    )
    analytics_url = admin.rsplit("/", 1)[0] + "/analytics"
    tables = [
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
        "analytics_seed_meta",
    ]
    with psycopg.connect(analytics_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("GRANT USAGE ON SCHEMA public TO analytics_readonly")
            for table in tables:
                cur.execute(f"GRANT SELECT ON TABLE {table} TO analytics_readonly")
            # Ensure no write grants for readonly
            for table in tables:
                cur.execute(
                    f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {table} FROM analytics_readonly"
                )


def main() -> int:
    env = os.environ.copy()
    env.setdefault(
        "PLATFORM_DATABASE_URL",
        "postgresql+psycopg://platform_app:platform_app@db:5432/platform",
    )
    env.setdefault(
        "ANALYTICS_MIGRATOR_URL",
        "postgresql+psycopg://analytics_migrator:analytics_migrator@db:5432/analytics",
    )

    # Bootstrap may already have run; safe to re-run.
    run([sys.executable, "scripts/bootstrap_db.py"], env=env)
    run(
        [sys.executable, "-m", "alembic", "-c", "alembic_platform.ini", "upgrade", "head"],
        env=env,
    )
    run(
        [sys.executable, "-m", "alembic", "-c", "alembic_analytics.ini", "upgrade", "head"],
        env=env,
    )
    run([sys.executable, "scripts/seed_analytics.py"], env=env)
    grant_readonly()
    print("migrate_and_seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
