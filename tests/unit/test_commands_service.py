"""Unit tests for dispatch_command (commands_service.py).

Tests run against in-memory SQLite via the shared conftest.
No HTTP client required — calls the service function directly.
"""

import pytest
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.commands_service import dispatch_command
from tests.conftest import TestingSessionLocal


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _dispatch(**kwargs) -> IntegrationInbox:
    async with TestingSessionLocal() as session:
        cmd = await dispatch_command(session=session, **kwargs)
        await session.commit()
        cmd_id = cmd.id
    # Re-fetch outside the session to verify it was persisted
    async with TestingSessionLocal() as session:
        return await session.get(IntegrationInbox, cmd_id)


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_creates_pending_command():
    """A dispatched command lands in the inbox with status='pending'."""
    cmd = await _dispatch(
        command_type="ticket.create",
        aggregate_type="ticket",
        aggregate_id="",
        payload={"subject": "Test", "message": "Hello"},
        requested_by_staff_id=1,
    )
    assert cmd is not None
    assert cmd.command_type == "ticket.create"
    assert cmd.status == "pending"
    assert cmd.requested_by_staff_id == 1


@pytest.mark.asyncio
async def test_dispatch_serialises_payload():
    """Payload dict is stored as JSON string."""
    import json

    payload = {"subject": "JSON test", "priority_id": 3}
    cmd = await _dispatch(
        command_type="ticket.create",
        aggregate_type="ticket",
        aggregate_id="",
        payload=payload,
    )
    assert json.loads(cmd.payload) == payload


@pytest.mark.asyncio
async def test_idempotency_key_returns_existing_command():
    """Re-submitting the same idempotency_key returns the original row."""
    key = "unique-key-abc123"
    cmd1 = await _dispatch(
        command_type="ticket.reply",
        aggregate_type="ticket",
        aggregate_id="42",
        payload={"message": "First send"},
        idempotency_key=key,
    )
    cmd2 = await _dispatch(
        command_type="ticket.reply",
        aggregate_type="ticket",
        aggregate_id="42",
        payload={"message": "Duplicate send"},
        idempotency_key=key,
    )
    assert cmd1.id == cmd2.id
    import json
    assert json.loads(cmd2.payload)["message"] == "First send"


@pytest.mark.asyncio
async def test_different_idempotency_keys_create_separate_commands():
    """Two commands with different keys create two separate rows."""
    cmd1 = await _dispatch(
        command_type="ticket.close",
        aggregate_type="ticket",
        aggregate_id="7",
        payload={},
        idempotency_key="key-one",
    )
    cmd2 = await _dispatch(
        command_type="ticket.close",
        aggregate_type="ticket",
        aggregate_id="7",
        payload={},
        idempotency_key="key-two",
    )
    assert cmd1.id != cmd2.id


@pytest.mark.asyncio
async def test_no_idempotency_key_always_creates_new_command():
    """Commands without an idempotency key are always inserted (no dedup)."""
    async with TestingSessionLocal() as session:
        for _ in range(3):
            await dispatch_command(
                session=session,
                command_type="ticket.note",
                aggregate_type="ticket",
                aggregate_id="5",
                payload={"title": "Note", "message": "Body"},
            )
        await session.commit()

    async with TestingSessionLocal() as session:
        result = await session.execute(
            select(IntegrationInbox).where(
                IntegrationInbox.command_type == "ticket.note"
            )
        )
        rows = result.scalars().all()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_staff_id_is_recorded():
    """requested_by_staff_id is persisted on the command."""
    cmd = await _dispatch(
        command_type="task.create",
        aggregate_type="task",
        aggregate_id="",
        payload={"title": "Do something"},
        requested_by_staff_id=99,
    )
    assert cmd.requested_by_staff_id == 99
