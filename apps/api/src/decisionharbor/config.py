from dataclasses import dataclass
import os
from pathlib import Path


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ApiSettings:
    platform_database_url: str
    analytics_readiness_database_url: str
    dataset_root: Path
    statement_timeout_ms: int
    max_rows: int

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            platform_database_url=os.environ["PLATFORM_DATABASE_URL"],
            analytics_readiness_database_url=os.environ["ANALYTICS_READINESS_DATABASE_URL"],
            dataset_root=Path(os.environ["DATASET_ROOT"]),
            statement_timeout_ms=_bounded_int("QUERY_STATEMENT_TIMEOUT_MS", 5_000, 1, 30_000),
            max_rows=_bounded_int("QUERY_MAX_ROWS", 500, 1, 5_000),
        )


@dataclass(frozen=True)
class WorkerSettings:
    platform_database_url: str
    analytics_database_url: str
    max_concurrency: int
    poll_ms: int

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(
            platform_database_url=os.environ["PLATFORM_WORKER_DATABASE_URL"],
            analytics_database_url=os.environ["ANALYTICS_DATABASE_URL"],
            max_concurrency=_bounded_int("QUERY_MAX_CONCURRENCY", 4, 1, 16),
            poll_ms=_bounded_int("WORKER_POLL_MS", 250, 1, 60_000),
        )
