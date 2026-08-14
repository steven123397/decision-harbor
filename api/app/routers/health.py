"""Liveness and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from ..config import settings
from ..container import catalog

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


def _readiness() -> tuple[bool, str | None]:
    try:
        platform = create_engine(settings.platform_writer_dsn, future=True)
        with platform.connect() as conn:
            row = conn.execute(text("SELECT version FROM dataset_seed LIMIT 1")).first()
        platform.dispose()
        if row is None:
            return False, "dataset not seeded"
        if row[0] != catalog.version:
            return False, f"dataset version mismatch: seeded={row[0]} expected={catalog.version}"

        analytics = create_engine(settings.analytics_reader_dsn, future=True)
        with analytics.connect() as conn:
            for name in catalog.tables:
                conn.execute(text(f'SELECT count(*) FROM "{catalog.schema}"."{name}"')).scalar()
        analytics.dispose()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


@router.get("/ready")
def ready():
    ok, reason = _readiness()
    if ok:
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason})
