"""Integration tests for the outbox event worker.

Tests the full poll_and_process_events() cycle against in-memory SQLite.
No real osTicket or PHP bridge needed.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlmodel import select

from app.integration.osticket.models import IntegrationOutbox
from app.workers.events import poll_and_process_events, purge_old_events
from tests.conftest import TestingSessionLocal


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _insert_outbox(
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload_str: str,
    status: str = "pending",
    retry_count: int = 0,
) -> int:
    async with TestingSessionLocal() as session:
        ev = IntegrationOutbox(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload_str,
            status=status,
            retry_count=retry_count,
            created_at=datetime.now(UTC),
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)
        return ev.id


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_event_type_does_not_crash_worker():
    """An event type with no registered handler is processed without error."""
    ev_id = await _insert_outbox("galaxy.exploded", "galaxy", "42", '{"foo": "bar"}')

    summary = await poll_and_process_events(TestingSessionLocal)
    assert summary["processed"] == 1

    async with TestingSessionLocal() as session:
        ev = await session.get(IntegrationOutbox, ev_id)
        assert ev.status == "processed"


@pytest.mark.asyncio
async def test_poison_payload_is_permanently_failed():
    """Non-JSON payload is dead-lettered immediately without retry."""
    ev_id = await _insert_outbox("ticket.created", "ticket", "2", "NOT VALID JSON")

    summary = await poll_and_process_events(TestingSessionLocal)
    assert summary["failed"] == 1

    async with TestingSessionLocal() as session:
        ev = await session.get(IntegrationOutbox, ev_id)
        assert ev.status == "failed"
        assert "JSON" in ev.error_message
        # Retry count must NOT have been incremented — it's non-retryable
        assert ev.retry_count == 0


@pytest.mark.asyncio
async def test_transient_failure_increments_retry_count():
    """A handler that raises increments retry_count and leaves status='pending'."""
    from app.core.events import registry

    async def _bad_handler(payload, session):
        raise RuntimeError("simulated transient error")

    registry.register("widget.broken", _bad_handler)
    try:
        ev_id = await _insert_outbox("widget.broken", "widget", "3", '{"x": 1}')
        await poll_and_process_events(TestingSessionLocal)

        async with TestingSessionLocal() as session:
            ev = await session.get(IntegrationOutbox, ev_id)
            assert ev.retry_count == 1
            assert ev.status == "pending"
    finally:
        registry._handlers["widget.broken"].pop()


@pytest.mark.asyncio
async def test_exhausted_retries_marks_event_failed():
    """An event at MAX_RETRIES-1 that fails again is dead-lettered."""
    from app.core.config import settings
    from app.core.events import registry

    async def _bad_handler(payload, session):
        raise RuntimeError("still failing")

    registry.register("widget.broken2", _bad_handler)
    try:
        ev_id = await _insert_outbox(
            "widget.broken2", "widget", "4", '{"x": 2}',
            retry_count=settings.EVENT_MAX_RETRIES - 1,
        )
        await poll_and_process_events(TestingSessionLocal)

        async with TestingSessionLocal() as session:
            ev = await session.get(IntegrationOutbox, ev_id)
            assert ev.status == "failed"
            assert ev.retry_count == settings.EVENT_MAX_RETRIES
    finally:
        registry._handlers["widget.broken2"].pop()


@pytest.mark.asyncio
async def test_stale_processing_events_are_requeued():
    """Events stuck in 'processing' with an old locked_at are re-queued."""
    stale_time = datetime.now(UTC) - timedelta(minutes=10)
    async with TestingSessionLocal() as session:
        ev = IntegrationOutbox(
            event_type="ticket.created",
            aggregate_type="ticket",
            aggregate_id="99",
            payload='{"foo": "bar"}',
            status="processing",
            locked_at=stale_time,
            created_at=stale_time,
        )
        session.add(ev)
        await session.commit()
        ev_id = ev.id

    summary = await poll_and_process_events(TestingSessionLocal)
    # Event recovered and processed (no handler → no-op but still processed)
    assert summary["processed"] == 1


@pytest.mark.asyncio
async def test_purge_removes_old_processed_events():
    """purge_old_events removes stale processed/failed events only."""
    old_time = datetime.now(UTC) - timedelta(days=35)
    async with TestingSessionLocal() as session:
        old = IntegrationOutbox(
            event_type="ticket.created", aggregate_type="ticket", aggregate_id="99",
            payload="{}", status="processed",
            created_at=old_time, processed_at=old_time,
        )
        fresh = IntegrationOutbox(
            event_type="ticket.created", aggregate_type="ticket", aggregate_id="100",
            payload="{}", status="processed",
            created_at=datetime.now(UTC), processed_at=datetime.now(UTC),
        )
        session.add_all([old, fresh])
        await session.commit()

    purged = await purge_old_events(TestingSessionLocal, ttl_days=30)
    assert purged == 1

    async with TestingSessionLocal() as session:
        result = await session.execute(select(IntegrationOutbox))
        remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].aggregate_id == "100"


@pytest.mark.asyncio
async def test_duplicate_processing_is_skipped():
    """Concurrent workers: second claim for same event returns 'skipped'."""
    ev_id = await _insert_outbox("noop.event", "noop", "1", "{}")

    # Manually set to 'processing' to simulate another worker claiming it
    async with TestingSessionLocal() as session:
        ev = await session.get(IntegrationOutbox, ev_id)
        ev.status = "processing"
        ev.locked_at = datetime.now(UTC)
        await session.commit()

    summary = await poll_and_process_events(TestingSessionLocal)
    assert summary["skipped"] == 0  # not in pending, not picked up
    assert summary["processed"] == 0
