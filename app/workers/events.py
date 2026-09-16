"""Outbox event worker.

Polls ``ost_integration_outbox`` for ``pending`` events, dispatches them to
registered Python handlers, and broadcasts processed events over WebSocket.

Reliability guarantees:
  - Atomic claim (UPDATE … WHERE status='pending') prevents duplicate processing
    across multiple API workers.
  - Stale-lock recovery: events stuck in ``processing`` for >5 min are re-queued.
  - Poison payload (bad JSON): marked ``failed`` immediately, no retry.
  - Transient handler failures: retry up to EVENT_MAX_RETRIES, then dead-letter.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.events import registry
from app.core.ws_manager import manager as ws_manager
from app.integration.osticket.models import IntegrationOutbox

logger = logging.getLogger(__name__)

_handlers_registered = False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Handler registration ───────────────────────────────────────────────────────


def _ensure_handlers_registered() -> None:
    """Lazily register all domain event handlers.

    Called before each processing cycle. Idempotent due to the ``_handlers_registered``
    guard and the EventRegistry's duplicate-check.
    """
    global _handlers_registered
    if _handlers_registered:
        return
    # Domain handlers are imported and registered here as each phase is implemented.
    # Phase 2+: from app.modules.tickets.service import register_event_handlers as _t; _t(registry)
    # Phase 3+: from app.modules.tasks.service import register_event_handlers as _tk; _tk(registry)
    # Phase 4+: from app.modules.users.service import register_event_handlers as _u; _u(registry)
    _handlers_registered = True


# ── Atomic claim ───────────────────────────────────────────────────────────────


async def _claim_event(session: AsyncSession, event_id: int, now: datetime) -> bool:
    """Atomically transition event from pending → processing.

    Returns True if this worker claimed the event, False if another beat it.
    """
    result = await session.execute(
        update(IntegrationOutbox)
        .where(IntegrationOutbox.id == event_id)
        .where(IntegrationOutbox.status == "pending")
        .values(status="processing", locked_at=now)
    )
    await session.commit()
    return result.rowcount == 1


# ── Single event processing ────────────────────────────────────────────────────


async def process_event(event_id: int, session_factory) -> tuple[str, dict | None]:
    """Process one outbox event end-to-end.

    Returns:
        A ``(status, broadcast_payload)`` tuple where ``status`` is one of
        ``"processed"``, ``"skipped"``, or ``"failed"``.
    """
    _ensure_handlers_registered()
    now = _now()

    async with session_factory() as claim_session:
        if not await _claim_event(claim_session, event_id, now):
            return "skipped", None

    async with session_factory() as session:
        event = await session.get(IntegrationOutbox, event_id)
        if event is None:
            return "failed", None

        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError) as exc:
            await _mark_failed(session_factory, event_id, f"Non-retryable JSON error: {exc}")
            return "failed", None

        try:
            await registry.dispatch(event.event_type, payload, session)
            event.status = "processed"
            event.processed_at = _now()
            event.locked_at = None
            event.error_message = None
            session.add(event)
            await session.commit()
            return "processed", {
                "type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "payload": payload,
            }
        except Exception as exc:
            await session.rollback()
            await _record_failure(session_factory, event_id, str(exc))
            logger.exception("Event %s failed", event_id)
            return "failed", None


# ── Failure helpers ────────────────────────────────────────────────────────────


async def _mark_failed(session_factory, event_id: int, error: str) -> None:
    async with session_factory() as session:
        event = await session.get(IntegrationOutbox, event_id)
        if event is None:
            return
        event.status = "failed"
        event.error_message = error
        event.processed_at = _now()
        event.locked_at = None
        await session.commit()


async def _record_failure(session_factory, event_id: int, error: str) -> None:
    async with session_factory() as session:
        event = await session.get(IntegrationOutbox, event_id)
        if event is None:
            return
        event.retry_count += 1
        event.error_message = error
        event.locked_at = None
        if event.retry_count >= settings.EVENT_MAX_RETRIES:
            event.status = "failed"
            event.processed_at = _now()
        else:
            event.status = "pending"
        await session.commit()


# ── Batch poll ─────────────────────────────────────────────────────────────────


async def poll_and_process_events(session_factory) -> dict:
    """Run one polling cycle: recover stale locks, then process pending events.

    Returns:
        Summary dict with ``processed``, ``failed``, ``skipped`` counts.
    """
    _ensure_handlers_registered()
    summary = {"processed": 0, "failed": 0, "skipped": 0}
    now = _now()
    stale_before = now - timedelta(minutes=5)

    async with session_factory() as session:
        # Re-queue events stuck in 'processing' (crashed worker recovery).
        await session.execute(
            update(IntegrationOutbox)
            .where(IntegrationOutbox.status == "processing")
            .where(
                (IntegrationOutbox.locked_at.is_(None))
                | (IntegrationOutbox.locked_at < stale_before)
            )
            .values(status="pending", locked_at=None)
        )
        await session.commit()

        result = await session.execute(
            select(IntegrationOutbox.id)
            .where(IntegrationOutbox.status == "pending")
            .order_by(IntegrationOutbox.created_at)
            .limit(settings.EVENT_BATCH_SIZE)
        )
        pending_ids = [row[0] for row in result.all()]

    for event_id in pending_ids:
        status, message = await process_event(event_id, session_factory)
        if status == "processed":
            summary["processed"] += 1
            try:
                await ws_manager.broadcast(message)
            except Exception:
                logger.exception("WebSocket broadcast failed for event %s", event_id)
        elif status == "skipped":
            summary["skipped"] += 1
        else:
            summary["failed"] += 1

    if pending_ids:
        logger.info("Poll cycle: %s", summary)
    return summary


# ── Purge ─────────────────────────────────────────────────────────────────────


async def purge_old_events(session_factory, ttl_days: int | None = None) -> int:
    """Delete processed/failed events older than ``ttl_days``.

    Returns the number of deleted rows.
    """
    ttl_days = ttl_days or settings.EVENT_RETENTION_DAYS
    cutoff = _now() - timedelta(days=ttl_days)
    async with session_factory() as session:
        result = await session.execute(
            select(IntegrationOutbox).where(
                IntegrationOutbox.status.in_(["processed", "failed"]),
                IntegrationOutbox.created_at < cutoff,
            )
        )
        events = result.scalars().all()
        for event in events:
            await session.delete(event)
        await session.commit()
    return len(events)
