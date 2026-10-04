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
from datetime import date, datetime
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.core.redaction import without_secrets

logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_LENGTH = 200   # the column holds 255; the staff prefix takes the rest


class IdempotencyKeyReused(ValueError):
    """The Idempotency-Key was already used for a different request; the API answers 422."""


class InvalidIdempotencyKey(ValueError):
    """The Idempotency-Key header is unusable (too long); the API answers 422."""


def scoped_idempotency_key(staff_id: int | None, key: str) -> str:
    """The key as stored: ``<staff id>:<key>``.

    Keys are unique per staff member, not globally. Otherwise one agent reusing another's key would be
    handed that agent's command back (its id, its status, its result); now the same key from two agents
    is two different commands.
    """
    return f"{staff_id or 0}:{key}"


def client_idempotency_key(stored: str | None) -> str | None:
    """Undo ``scoped_idempotency_key`` for display."""
    return stored.partition(":")[2] if stored and ":" in stored else stored


def _json_default(value: Any) -> str:
    """Encode the non-JSON types a Pydantic ``model_dump()`` can contain (dates as ISO 8601).

    Without this, any command body with a datetime field (e.g. a task ``due_date``) made
    ``json.dumps`` raise and the route answer 500 before anything was queued.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _same_request(existing: IntegrationInbox, command_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> bool:
    """Is a repeated key a retry of the stored command (same type, resource and body)? Secret fields are left out of
    the comparison: the bridge overwrites them once the command finishes, so they cannot be compared."""
    if (existing.command_type, existing.aggregate_type, existing.aggregate_id) != (command_type, aggregate_type, aggregate_id):
        return False
    try:
        stored = json.loads(existing.payload)
    except (TypeError, ValueError):
        return False
    return without_secrets(stored) == without_secrets(json.loads(json.dumps(payload, default=_json_default)))


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
        session: Active AsyncSession (the command is committed here so that it is visible, and the bridge
                 can claim it, before the route answers).
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
    # ── Idempotency check (per staff member) ──────────────────────────────────
    if idempotency_key:
        if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise InvalidIdempotencyKey(
                f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
            )
        stored_key = scoped_idempotency_key(requested_by_staff_id, idempotency_key)
        result = await session.execute(
            select(IntegrationInbox).where(
                IntegrationInbox.idempotency_key == stored_key
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if not _same_request(existing, command_type, aggregate_type, aggregate_id, payload):
                raise IdempotencyKeyReused(
                    "This Idempotency-Key was already used for a different request; use a new key for a new request."
                )
            logger.info(
                "Idempotency hit for key=%s command_id=%s", stored_key, existing.id
            )
            return existing

    # ── Create new command ────────────────────────────────────────────────────
    command = IntegrationInbox(
        command_type=command_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload, default=_json_default),
        idempotency_key=stored_key if idempotency_key else None,
        requested_by_staff_id=requested_by_staff_id,
    )
    session.add(command)
    try:
        # Committed here, before the route answers 202: FastAPI runs the session dependency's own commit after the
        # response is sent, so a client that polls GET /commands/{id} at once could otherwise not see the row.
        await session.commit()
    except IntegrityError:
        await session.rollback()    # a concurrent request took the same Idempotency-Key: that one is the command
        winner = (await session.execute(select(IntegrationInbox).where(IntegrationInbox.idempotency_key == stored_key))).scalar_one()
        if not _same_request(winner, command_type, aggregate_type, aggregate_id, payload):
            raise IdempotencyKeyReused("This Idempotency-Key was already used for a different request; use a new key for a new request.")
        return winner

    logger.info(
        "Command queued: id=%s type=%s aggregate=%s/%s",
        command.id,
        command_type,
        aggregate_type,
        aggregate_id,
    )
    return command
