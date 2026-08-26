from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from decisionharbor.dataset import DatasetContract


READINESS_CONNECT_TIMEOUT_SECONDS = 1
READINESS_STATEMENT_TIMEOUT_MS = 1_000
API_ROOT = Path(__file__).resolve().parents[2]


def migration_head(config_path: Path) -> str:
    heads = ScriptDirectory.from_config(Config(str(config_path))).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected one migration head, found {len(heads)}")
    return heads[0]


def _readiness_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": READINESS_CONNECT_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={READINESS_STATEMENT_TIMEOUT_MS}",
        },
    )


class PlatformReadinessProbe:
    def __init__(
        self,
        database_url: str,
        migration_config: Path = API_ROOT / "alembic-platform.ini",
    ) -> None:
        self._engine = _readiness_engine(database_url)
        self._migration_config = migration_config

    def check_ready(self) -> bool:
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
                    == migration_head(self._migration_config)
                )
        except Exception:
            return False


class AnalyticsReadinessProbe:
    def __init__(
        self,
        database_url: str,
        migration_config: Path = API_ROOT / "alembic-analytics.ini",
    ) -> None:
        self._engine = _readiness_engine(database_url)
        self._migration_config = migration_config

    def check_ready(self, dataset: DatasetContract) -> bool:
        try:
            with self._engine.connect() as connection:
                read_only = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
                migration = connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                marker = connection.execute(
                    text(
                        """
                        SELECT contract_sha256, manifest_sha256, row_counts
                        FROM maintenance.dataset_seeds
                        WHERE dataset = :dataset AND version = :version
                        """
                    ),
                    {
                        "dataset": dataset.manifest["dataset"],
                        "version": dataset.manifest["version"],
                    },
                ).one_or_none()
                return bool(
                    read_only == "on"
                    and migration == migration_head(self._migration_config)
                    and marker
                    and marker[0].strip() == dataset.contract_sha256
                    and marker[1].strip() == dataset.manifest_sha256
                    and marker[2] == dataset.contract["expected_counts"]
                )
        except Exception:
            return False
