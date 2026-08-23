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
class Settings:
    """API 进程配置：只持有 platform 凭据与就绪探测凭据。"""

    platform_database_url: str
    analytics_readiness_database_url: str
    dataset_root: Path
    statement_timeout_ms: int
    max_rows: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            platform_database_url=os.environ["PLATFORM_DATABASE_URL"],
            analytics_readiness_database_url=os.environ["ANALYTICS_READINESS_DATABASE_URL"],
            dataset_root=Path(os.environ["DATASET_ROOT"]),
            statement_timeout_ms=_bounded_int("QUERY_STATEMENT_TIMEOUT_MS", 5_000, 1, 30_000),
            max_rows=_bounded_int("QUERY_MAX_ROWS", 500, 1, 5_000),
        )


@dataclass(frozen=True)
class WorkerSettings:
    """Worker 进程配置：platform 任务凭据与 analytics 查询凭据。"""

    platform_database_url: str
    analytics_database_url: str
    analytics_readiness_database_url: str
    dataset_root: Path
    max_concurrency: int
    lease_ms: int
    heartbeat_ms: int
    poll_ms: int
    max_execution_attempts: int
    http_port: int
    cleanup_interval_ms: int

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        lease_ms = _bounded_int("WORKER_LEASE_MS", 15_000, 1, 3_600_000)
        heartbeat_ms = _bounded_int("WORKER_HEARTBEAT_MS", 3_000, 1, 3_600_000)
        if heartbeat_ms >= lease_ms:
            raise ValueError("WORKER_HEARTBEAT_MS must be strictly less than WORKER_LEASE_MS")
        return cls(
            platform_database_url=os.environ["PLATFORM_DATABASE_URL"],
            analytics_database_url=os.environ["ANALYTICS_DATABASE_URL"],
            analytics_readiness_database_url=os.environ["ANALYTICS_READINESS_DATABASE_URL"],
            dataset_root=Path(os.environ["DATASET_ROOT"]),
            max_concurrency=_bounded_int("QUERY_MAX_CONCURRENCY", 4, 1, 16),
            lease_ms=lease_ms,
            heartbeat_ms=heartbeat_ms,
            poll_ms=_bounded_int("WORKER_POLL_MS", 250, 1, 60_000),
            max_execution_attempts=_bounded_int("WORKER_MAX_EXECUTION_ATTEMPTS", 3, 1, 10),
            http_port=_bounded_int("WORKER_HTTP_PORT", 8001, 1, 65_535),
            cleanup_interval_ms=_bounded_int("WORKER_CLEANUP_INTERVAL_MS", 300_000, 1, 86_400_000),
        )
