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
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.db import AsyncSessionLocal, create_db_and_tables
from app.core.rate_limit import limiter
from app.core.request_limits import BodySizeLimitMiddleware
from app.workers.events import poll_and_process_events, purge_old_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Rate limiter (singleton imported from app.core.rate_limit) ───────────────
# Imported above — do not construct a second instance here.


# ── Background outbox worker ──────────────────────────────────────────────────


def _tables_missing(exc: Exception) -> bool:
    """True when ``exc`` is 'the integration tables don't exist (yet)' — MySQL or SQLite wording."""
    return "doesn't exist" in str(exc) or "no such table" in str(exc)


async def _outbox_worker() -> None:
    """Continuously poll the integration outbox and dispatch events."""
    logger.info("Outbox worker started (interval=%.1fs)", settings.EVENT_POLL_INTERVAL_SECONDS)
    warned_missing = False
    while True:
        try:
            await poll_and_process_events(AsyncSessionLocal)
            warned_missing = False
        except Exception as exc:
            if _tables_missing(exc):
                # The bridge plugin creates the tables when it is enabled; until then just wait.
                if not warned_missing:
                    logger.warning(
                        "Integration tables not found (prefix %r) — is the fast-ticket bridge plugin "
                        "installed and enabled in osTicket? Waiting for it.",
                        settings.OSTICKET_TABLE_PREFIX,
                    )
                    warned_missing = True
            else:
                logger.exception("Outbox worker encountered an unhandled error — continuing.")
        await asyncio.sleep(settings.EVENT_POLL_INTERVAL_SECONDS)


async def _purge_worker() -> None:
    """Hourly retention purge of finished inbox/outbox rows."""
    while True:
        try:
            deleted = await purge_old_events(AsyncSessionLocal)
            if deleted:
                logger.info("Purged %d old outbox events", deleted)
        except Exception as exc:
            if not _tables_missing(exc):   # the outbox worker already warns about missing tables
                logger.exception("Retention purge failed — will retry next hour.")
        await asyncio.sleep(3600)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_SCHEMA:
        logger.info("AUTO_CREATE_SCHEMA=true — ensuring integration tables exist.")
        await create_db_and_tables()

    worker_task = asyncio.create_task(_outbox_worker(), name="osticket-outbox-worker")
    purge_task = asyncio.create_task(_purge_worker(), name="osticket-purge-worker")
    logger.info("Application startup complete.")

    yield

    for task in (worker_task, purge_task):
        task.cancel()
        try:
            await task
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

# Rate limiter — SlowAPIMiddleware is what enforces the default limit on every route; without it
# only routes carrying an explicit @limiter.limit decorator (login) would be limited.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BYTES)

# CORS (added last so it is outermost: 413/429 responses still carry the CORS headers)
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
