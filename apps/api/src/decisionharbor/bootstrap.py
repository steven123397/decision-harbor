import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql

from decisionharbor.dataset import load_dataset, run_public_validator
from decisionharbor.seed import seed_dataset


ROOT = Path(__file__).resolve().parents[2]


def _database_url(admin_url: str, database: str, *, sqlalchemy: bool = False) -> str:
    parsed = urlsplit(admin_url)
    scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
    return urlunsplit((scheme, parsed.netloc, f"/{database}", "", ""))


def _ensure_roles_and_databases(
    admin_url: str,
    platform_password: str,
    platform_worker_password: str,
    analytics_password: str,
    readiness_password: str,
) -> None:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for role, password in (
                ("platform_app", platform_password),
                ("platform_worker", platform_worker_password),
                ("analytics_reader", analytics_password),
                ("analytics_readiness", readiness_password),
            ):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                else:
                    cursor.execute(
                        sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
            cursor.execute("ALTER ROLE analytics_reader SET default_transaction_read_only = on")
            cursor.execute("ALTER ROLE analytics_readiness SET default_transaction_read_only = on")
            for database in ("platform", "analytics"):
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} OWNER harbor_admin ENCODING 'UTF8'").format(
                            sql.Identifier(database)
                        )
                    )

            cursor.execute(
                "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC, platform_app, platform_worker, analytics_reader, analytics_readiness"
            )
            cursor.execute(
                "REVOKE CONNECT ON DATABASE template1 FROM PUBLIC, platform_app, platform_worker, analytics_reader, analytics_readiness"
            )
            cursor.execute("REVOKE CONNECT ON DATABASE platform FROM PUBLIC, analytics_reader, analytics_readiness")
            cursor.execute("GRANT CONNECT ON DATABASE platform TO platform_app, platform_worker")
            cursor.execute(
                "REVOKE CONNECT ON DATABASE analytics FROM PUBLIC, platform_app, platform_worker"
            )
            cursor.execute(
                "REVOKE TEMPORARY ON DATABASE analytics FROM PUBLIC, analytics_reader, analytics_readiness"
            )
            cursor.execute("GRANT CONNECT ON DATABASE analytics TO analytics_reader, analytics_readiness")
            cursor.execute(
                "ALTER ROLE analytics_reader IN DATABASE analytics SET search_path = analytics, public"
            )
            cursor.execute(
                "ALTER ROLE analytics_readiness IN DATABASE analytics SET search_path = maintenance, public"
            )


def _migrate(config_path: Path, database_url: str) -> None:
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _harden_public_schema(database_url: str, runtime_roles: tuple[str, ...]) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            for runtime_role in runtime_roles:
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(runtime_role))
                )


def main() -> None:
    admin_url = os.environ["ADMIN_DATABASE_URL"]
    dataset_root = Path(os.environ["DATASET_ROOT"])
    platform_password = os.environ["PLATFORM_APP_PASSWORD"]
    platform_worker_password = os.environ["PLATFORM_WORKER_PASSWORD"]
    analytics_password = os.environ["ANALYTICS_READER_PASSWORD"]
    readiness_password = os.environ["ANALYTICS_READINESS_PASSWORD"]

    run_public_validator(dataset_root)
    dataset = load_dataset(dataset_root)
    _ensure_roles_and_databases(
        admin_url,
        platform_password,
        platform_worker_password,
        analytics_password,
        readiness_password,
    )

    platform_admin_url = _database_url(admin_url, "platform")
    analytics_admin_url = _database_url(admin_url, "analytics")
    _migrate(ROOT / "alembic-platform.ini", _database_url(admin_url, "platform", sqlalchemy=True))
    _migrate(ROOT / "alembic-analytics.ini", _database_url(admin_url, "analytics", sqlalchemy=True))
    _harden_public_schema(platform_admin_url, ("platform_app", "platform_worker"))
    _harden_public_schema(analytics_admin_url, ("analytics_readiness",))
    result = seed_dataset(analytics_admin_url, dataset)
    print(f"bootstrap complete: dataset {result}")


if __name__ == "__main__":
    main()
