"""Unit tests for dispatch_command (commands_service.py).

Tests run against in-memory SQLite via the shared conftest.
No HTTP client required — calls the service function directly.
"""

import json

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
    """Re-submitting the same request with the same idempotency_key returns the original row."""
    key = "unique-key-abc123"
    request = dict(command_type="ticket.reply", aggregate_type="ticket", aggregate_id="42",
                   payload={"message": "First send"}, idempotency_key=key)
    cmd1 = await _dispatch(**request)
    cmd2 = await _dispatch(**request)
    assert cmd1.id == cmd2.id


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"payload": {"message": "Another message"}},
    {"aggregate_id": "43"},
    {"command_type": "ticket.note"},
])
async def test_reusing_a_key_for_a_different_request_is_refused(change):
    from app.integration.osticket.commands_service import IdempotencyKeyReused
    request = dict(command_type="ticket.reply", aggregate_type="ticket", aggregate_id="42",
                   payload={"message": "First send"}, idempotency_key="reused-key")
    await _dispatch(**request)
    with pytest.raises(IdempotencyKeyReused):
        await _dispatch(**{**request, **change})


@pytest.mark.asyncio
async def test_a_retry_matches_even_after_the_bridge_scrubbed_the_secret():
    """The bridge overwrites a password in the stored row when the command finishes; the retry still carries it."""
    from sqlmodel import select
    from app.integration.osticket.commands import IntegrationInbox
    from tests.conftest import TestingSessionLocal
    request = dict(command_type="staff.create", aggregate_type="staff", aggregate_id="",
                   payload={"username": "a", "password": "S3cret-pass"}, idempotency_key="staff-key")
    first = await _dispatch(**request)
    async with TestingSessionLocal() as session:
        row = (await session.execute(select(IntegrationInbox).where(IntegrationInbox.id == first.id))).scalar_one()
        row.payload = '{"username": "a", "password": "[redacted]"}'
        await session.commit()
    assert (await _dispatch(**request)).id == first.id
    with pytest.raises(Exception):
        await _dispatch(**{**request, "payload": {"username": "b", "password": "S3cret-pass"}})


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


@pytest.mark.asyncio
async def test_datetime_payload_values_are_serialised_as_iso_8601():
    """A command body with a datetime field (a task due_date) must queue, not raise."""
    from datetime import datetime

    async with TestingSessionLocal() as session:
        cmd = await dispatch_command(
            session=session,
            command_type="task.update",
            aggregate_type="task",
            aggregate_id="1",
            payload={"task_id": 1, "due_date": datetime(2030, 1, 15, 10, 0, 0)},
            requested_by_staff_id=1,
        )
        await session.commit()

    assert json.loads(cmd.payload)["due_date"] == "2030-01-15T10:00:00"


@pytest.mark.asyncio
async def test_the_command_is_committed_before_dispatch_returns():
    """A client may poll GET /commands/{id} the moment the route answers 202, before the session dependency's own
    commit would run — so the row must already be visible to another connection."""
    async with TestingSessionLocal() as writer:
        command = await dispatch_command(
            session=writer, command_type="ticket.close", aggregate_type="ticket", aggregate_id="1", payload={},
            requested_by_staff_id=1,
        )                                              # no commit by the caller
        async with TestingSessionLocal() as reader:
            seen = (await reader.execute(select(IntegrationInbox).where(IntegrationInbox.id == command.id))).scalar_one_or_none()
    assert seen is not None and seen.status == "pending"
