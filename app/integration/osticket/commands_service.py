"""Command dispatch — the single entry point for write operations.

All state changes in osTicket must go through ``dispatch_command``.  This
writes a row to ``ost_integration_inbox`` which the PHP cron bridge picks up
and executes against the osTicket PHP model layer.

Idempotency
-----------
If an ``idempotency_key`` is supplied and a row with that key already exists,
the existing row is returned without creating a duplicate.  Callers should
include a client-generated ``Idempotency-Key`` header on every write request.
"""

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox

logger = logging.getLogger(__name__)


async def dispatch_command(
    *,
    session: AsyncSession,
    command_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    requested_by_staff_id: int | None = None,
    idempotency_key: str | None = None,
) -> IntegrationInbox:
    """Queue a command for PHP-side execution.

    Args:
        session: Active AsyncSession (will be flushed but NOT committed here;
                 the route's session dependency handles the final commit).
        command_type: Dot-namespaced command identifier, e.g. ``"ticket.reply"``.
        aggregate_type: Domain name of the resource, e.g. ``"ticket"``.
        aggregate_id: String representation of the resource's osTicket PK.
                      Use ``""`` for creation commands where the ID is unknown.
        payload: Dict of command-specific parameters (must be JSON-serialisable).
        requested_by_staff_id: osTicket staff_id of the authenticated requester.
        idempotency_key: Client-supplied deduplication key.

    Returns:
        The newly created (or pre-existing) :class:`IntegrationInbox` row.
    """
    # ── Idempotency check ─────────────────────────────────────────────────────
    if idempotency_key:
        result = await session.execute(
            select(IntegrationInbox).where(
                IntegrationInbox.idempotency_key == idempotency_key
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            logger.info(
                "Idempotency hit for key=%s command_id=%s", idempotency_key, existing.id
            )
            return existing

    # ── Create new command ────────────────────────────────────────────────────
    command = IntegrationInbox(
        command_type=command_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload),
        idempotency_key=idempotency_key,
        requested_by_staff_id=requested_by_staff_id,
    )
    session.add(command)
    await session.flush()   # populate .id without committing

    logger.info(
        "Command queued: id=%s type=%s aggregate=%s/%s",
        command.id,
        command_type,
        aggregate_type,
        aggregate_id,
    )
    return command
