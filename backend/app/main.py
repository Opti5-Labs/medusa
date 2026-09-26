"""
FastAPI application entry point.

Registers routers, CORS, and global exception handlers.
All business logic lives in app/api/* and app/pipelines/*.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import architecture as architecture_router
from app.api import ask as ask_router
from app.api import health as health_router
from app.api import runs as runs_router
from app.api import scan as scan_router
from app.config import ALLOWED_ORIGIN
from app.errors import MedusaError, medusa_error_handler
from app.store import RunStore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

# ── Application store (singleton) ──────────────────────────────────────────────

store = RunStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.start()
    scan_router.set_store(store)
    runs_router.set_store(store)
    ask_router.set_store(store)
    architecture_router.set_store(store)
    log.info("Medusa started — store TTL sweeper running")
    yield
    await store.stop()
    log.info("Medusa stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Medusa", version="0.2.0", lifespan=lifespan)

# ── Exception handlers ─────────────────────────────────────────────────────────

app.add_exception_handler(MedusaError, medusa_error_handler)  # type: ignore[arg-type]

# ── CORS ───────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=False,  # no cookies used
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health_router.router)
app.include_router(scan_router.router)
app.include_router(runs_router.router)
app.include_router(ask_router.router)
app.include_router(architecture_router.router)
