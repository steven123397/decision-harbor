"""FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .container import store
from .routers import health, query_runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.reconcile_stale_running()
    yield


app = FastAPI(title="DecisionHarbor API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(query_runs.router)
