"""Application factory and lifespan.

Startup sequence:
  1. Validate settings (config.py raises on invalid/missing values).
  2. Optionally create integration tables (AUTO_CREATE_SCHEMA).
  3. Launch the outbox event polling loop.

The outbox worker runs as a background asyncio task — it polls
``ost_integration_outbox`` every EVENT_POLL_INTERVAL_SECONDS seconds.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.router import api_router
from app.core.config import settings
from app.core.db import AsyncSessionLocal, create_db_and_tables
from app.workers.events import poll_and_process_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


# ── Background outbox worker ──────────────────────────────────────────────────


async def _outbox_worker() -> None:
    """Continuously poll the integration outbox and dispatch events."""
    logger.info("Outbox worker started (interval=%.1fs)", settings.EVENT_POLL_INTERVAL_SECONDS)
    while True:
        try:
            await poll_and_process_events(AsyncSessionLocal)
        except Exception:
            logger.exception("Outbox worker encountered an unhandled error — continuing.")
        await asyncio.sleep(settings.EVENT_POLL_INTERVAL_SECONDS)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_SCHEMA:
        logger.info("AUTO_CREATE_SCHEMA=true — ensuring integration tables exist.")
        await create_db_and_tables()

    worker_task = asyncio.create_task(_outbox_worker(), name="osticket-outbox-worker")
    logger.info("Application startup complete.")

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Application shutdown complete.")


# ── App factory ────────────────────────────────────────────────────────────────


app = FastAPI(
    title="fast-ticket",
    summary="FastAPI wrapper over osTicket — REST API for the osTicket integration bridge.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

# Routes
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["system"], summary="Health check")
async def health():
    """Unauthenticated health check endpoint for load balancers and Docker health checks."""
    return {"status": "ok"}
