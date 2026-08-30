from dataclasses import dataclass
import os
from uuid import uuid4


class WorkerConfigurationError(ValueError):
    pass


def _required_url(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise WorkerConfigurationError(f"{name} is required")
    return value


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise WorkerConfigurationError(f"{name} must be a positive integer, got {raw_value!r}") from exc
    if not 1 <= value <= maximum:
        raise WorkerConfigurationError(
            f"{name} must be a positive integer not greater than {maximum}, got {value}"
        )
    return value


@dataclass(frozen=True)
class WorkerSettings:
    worker_id: str
    platform_database_url: str
    analytics_database_url: str
    max_concurrency: int
    lease_ms: int
    heartbeat_ms: int
    poll_ms: int
    max_execution_attempts: int
    cleanup_interval_ms: int

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        settings = cls(
            worker_id=os.getenv("WORKER_ID") or f"worker-{uuid4().hex[:12]}",
            platform_database_url=_required_url("PLATFORM_DATABASE_URL"),
            analytics_database_url=_required_url("ANALYTICS_DATABASE_URL"),
            max_concurrency=_positive_int("QUERY_MAX_CONCURRENCY", 4, 64),
            lease_ms=_positive_int("WORKER_LEASE_MS", 15_000, 600_000),
            heartbeat_ms=_positive_int("WORKER_HEARTBEAT_MS", 3_000, 600_000),
            poll_ms=_positive_int("WORKER_POLL_MS", 250, 60_000),
            max_execution_attempts=_positive_int("WORKER_MAX_EXECUTION_ATTEMPTS", 3, 10),
            cleanup_interval_ms=_positive_int("WORKER_CLEANUP_INTERVAL_MS", 60_000, 86_400_000),
        )
        if settings.heartbeat_ms >= settings.lease_ms:
            raise WorkerConfigurationError(
                "WORKER_HEARTBEAT_MS must be strictly less than WORKER_LEASE_MS, "
                f"got {settings.heartbeat_ms} and {settings.lease_ms}"
            )
        return settings
