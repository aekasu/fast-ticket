"""Outbox event worker — one cursor per API process.

Every API process reads ``ost_integration_outbox`` by its own position (``EventCursor``), so each event reaches
**every** process: the WebSocket clients connected to any worker are told, and every process runs the registered
event handlers (a per-process cache invalidates itself this way). The outbox is a log, not a work queue — rows are
not claimed or retried, and the ``status`` / ``retry_count`` / ``locked_at`` columns are unused by the API.

  * A process starts at the end of the log: it does not replay history to clients that connect later. Events written
    while a process was down are not delivered to it (WebSocket delivery is best-effort; clients refetch on reconnect).
  * Rows can commit out of id order (two concurrent inserts), so each poll re-reads a short window behind the cursor
    and skips ids already handled, instead of trusting ``id > last`` alone.
  * A handler that raises is logged and does not stop the others or the delivery; a payload that is not JSON is
    counted as failed and skipped.
  * Delivery is per client (``app.modules.events.visibility``): an event reaches only the agents allowed to hear it.
  * ``purge_old_events`` deletes rows by age.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.events import registry
from app.core.ws_manager import manager as ws_manager
from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox
from app.modules.events import visibility

logger = logging.getLogger(__name__)

OVERLAP = 100          # ids re-read behind the cursor, to catch a row that committed late
_handlers_registered = False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── This process's position in the log ─────────────────────────────────────────


@dataclass
class EventCursor:
    last_id: int | None = None                 # None: not started yet
    seen: set[int] = field(default_factory=set)

    def reset(self, last_id: int | None = None) -> None:
        self.last_id = last_id
        self.seen.clear()


cursor = EventCursor()


# ── Handler registration ───────────────────────────────────────────────────────


def _ensure_handlers_registered() -> None:
    """Lazily register all domain event handlers (none yet; Phase 4's cache invalidation registers here)."""
    global _handlers_registered
    if _handlers_registered:
        return
    _handlers_registered = True


# ── Delivery ───────────────────────────────────────────────────────────────────


async def deliver_event(session, message: dict) -> int:
    """Send ``message`` to the connected clients who may hear it. Returns how many received it."""
    clients = ws_manager.clients()
    if not clients:
        return 0
    audience = await visibility.recipients(session, message, clients)
    return await ws_manager.send(audience, message)


Deliver = Callable[[object, dict], Awaitable[int]]


# ── Poll ───────────────────────────────────────────────────────────────────────


async def poll_and_process_events(
    session_factory, *, position: EventCursor | None = None, deliver: Deliver = deliver_event
) -> dict:
    """One polling cycle: read what is new since this process's cursor, run handlers, deliver.

    Returns ``processed`` / ``failed`` counts and how many client deliveries were made.
    """
    _ensure_handlers_registered()
    position = position or cursor
    summary = {"processed": 0, "failed": 0, "delivered": 0}

    async with session_factory() as session:
        if position.last_id is None:                       # first poll: start at the end of the log
            position.last_id = (
                await session.execute(select(func.coalesce(func.max(IntegrationOutbox.id), 0)))
            ).scalar_one()
            # what is already in the overlap window is history, not late arrivals
            position.seen = set(
                (
                    await session.execute(
                        select(IntegrationOutbox.id).where(IntegrationOutbox.id > position.last_id - OVERLAP)
                    )
                ).scalars()
            )
            return summary

        # Late arrivals: ids in the window behind the cursor that appeared after we passed them. Read as ids first,
        # so already-handled rows cannot fill a batch and starve the new ones.
        window = set(
            (
                await session.execute(
                    select(IntegrationOutbox.id).where(
                        IntegrationOutbox.id > position.last_id - OVERLAP, IntegrationOutbox.id <= position.last_id
                    )
                )
            ).scalars()
        )
        late = window - position.seen
        rows = list(
            (
                await session.execute(
                    select(IntegrationOutbox)
                    .where(IntegrationOutbox.id > position.last_id)
                    .order_by(IntegrationOutbox.id)
                    .limit(settings.EVENT_BATCH_SIZE)
                )
            ).scalars()
        )
        if late:
            rows += list(
                (await session.execute(select(IntegrationOutbox).where(IntegrationOutbox.id.in_(late)))).scalars()
            )
        fresh = sorted((row for row in rows if row.id not in position.seen), key=lambda row: row.id)

        for event in fresh:
            position.seen.add(event.id)
            position.last_id = max(position.last_id, event.id)
            try:
                payload = json.loads(event.payload)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Outbox event %s has a payload that is not JSON — skipped", event.id)
                summary["failed"] += 1
                continue

            try:
                await registry.dispatch(event.event_type, payload, session)
            except Exception:
                logger.exception("Handler failed for outbox event %s (%s)", event.id, event.event_type)

            message = {
                "type": event.event_type,
                "aggregate_type": event.aggregate_type,
                # a create's event is queued with no id (the subject did not exist yet): take it from the result
                "aggregate_id": visibility.resolve_aggregate_id(event.aggregate_type, event.aggregate_id, payload),
                "payload": payload,
            }
            try:
                summary["delivered"] += await deliver(session, message)
            except Exception:
                logger.exception("WebSocket delivery failed for outbox event %s", event.id)
            summary["processed"] += 1

        position.seen = {i for i in position.seen if i > position.last_id - 2 * OVERLAP}

    if fresh:
        logger.info("Poll cycle: %s", summary)
    return summary


# ── Purge ─────────────────────────────────────────────────────────────────────


async def purge_old_events(session_factory, ttl_days: int | None = None) -> int:
    """Bulk-delete outbox events, and finished inbox commands, older than ``ttl_days``.

    The outbox is a log read by cursor, so age alone decides; the inbox keeps pending/processing rows.
    Returns the number of outbox rows deleted.
    """
    ttl_days = ttl_days or settings.EVENT_RETENTION_DAYS
    cutoff = _now() - timedelta(days=ttl_days)
    async with session_factory() as session:
        outbox = await session.execute(delete(IntegrationOutbox).where(IntegrationOutbox.created_at < cutoff))
        await session.execute(
            delete(IntegrationInbox).where(
                IntegrationInbox.status.in_(["done", "failed"]),
                IntegrationInbox.created_at < cutoff,
            )
        )
        await session.commit()
    return outbox.rowcount
