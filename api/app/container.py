"""Application singletons shared by routers."""
from __future__ import annotations

from pathlib import Path

from .config import settings
from .schema_catalog import load_catalog
from .service import QueryService, ResultCache
from .store import QueryRunStore


def build_catalog():
    return load_catalog(Path(settings.dataset_dir) / "contract.json")


catalog = build_catalog()
store = QueryRunStore(settings.platform_writer_dsn)
service = QueryService(store, ResultCache(), catalog)
