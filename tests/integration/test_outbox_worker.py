"""The outbox event worker: one cursor per API process, read against in-memory SQLite.

No real osTicket or PHP bridge needed — the outbox is just a table the tests write to.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.events import registry
from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox
from app.workers.events import OVERLAP, EventCursor, poll_and_process_events, purge_old_events
from tests.conftest import TestingSessionLocal


def _utcnow() -> datetime:
    """Naive UTC, matching what production stores (MySQL DATETIME, PHP NOW())."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _insert(event_type="ticket.created", aggregate_type="ticket", aggregate_id="1", payload='{"a": 1}',
                  *, id=None, created_at=None, status="pending") -> int:
    async with TestingSessionLocal() as session:
        row = IntegrationOutbox(
            id=id, event_type=event_type, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            payload=payload, status=status, created_at=created_at or _utcnow(),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


class Recorder:
    """A stand-in for WebSocket delivery that records what would have been sent."""

    def __init__(self, reach=1):
        self.messages: list[dict] = []
        self.reach = reach

    async def __call__(self, session, message):
        self.messages.append(message)
        return self.reach


async def _poll(position, recorder):
    return await poll_and_process_events(TestingSessionLocal, position=position, deliver=recorder)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(registry, "_handlers", {})


# ── the cursor ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_process_starts_at_the_end_of_the_log_and_does_not_replay_history():
    await _insert(aggregate_id="old")
    position, recorder = EventCursor(), Recorder()

    first = await _poll(position, recorder)
    assert first == {"processed": 0, "failed": 0, "delivered": 0}
    assert recorder.messages == []                       # history is not replayed to clients that connect later

    await _insert(aggregate_id="new")
    summary = await _poll(position, recorder)
    assert summary["processed"] == 1
    assert [m["aggregate_id"] for m in recorder.messages] == ["new"]


@pytest.mark.asyncio
async def test_an_event_is_delivered_once_by_a_process_however_often_it_polls():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert()
    for _ in range(4):
        await _poll(position, recorder)
    assert len(recorder.messages) == 1


@pytest.mark.asyncio
async def test_every_process_receives_every_event():
    """The old per-event claim gave each event to ONE worker, so clients on the others never heard of it.
    Each process has its own cursor now; two of them read the same log independently."""
    process_a, process_b = EventCursor(), EventCursor()
    heard_a, heard_b = Recorder(), Recorder()
    await _poll(process_a, heard_a)
    await _poll(process_b, heard_b)

    await _insert(aggregate_id="1")
    await _insert(aggregate_id="2")
    await _poll(process_a, heard_a)
    await _poll(process_b, heard_b)

    assert [m["aggregate_id"] for m in heard_a.messages] == ["1", "2"]
    assert [m["aggregate_id"] for m in heard_b.messages] == ["1", "2"]


@pytest.mark.asyncio
async def test_a_row_that_commits_late_with_a_lower_id_is_still_delivered():
    """Two concurrent inserts can commit out of id order; trusting `id > last` would skip the earlier one."""
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert(id=50, aggregate_id="fifty")
    await _poll(position, recorder)
    await _insert(id=48, aggregate_id="forty-eight")          # allocated before 50, committed after
    await _poll(position, recorder)
    assert sorted(m["aggregate_id"] for m in recorder.messages) == ["fifty", "forty-eight"]

    await _poll(position, recorder)
    assert len(recorder.messages) == 2                          # and not delivered a second time


@pytest.mark.asyncio
async def test_a_row_far_behind_the_cursor_is_not_reread():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert(id=OVERLAP * 5, aggregate_id="ahead")
    await _poll(position, recorder)
    await _insert(id=OVERLAP * 5 - OVERLAP - 10, aggregate_id="too-late")   # outside the re-read window
    await _poll(position, recorder)
    assert [m["aggregate_id"] for m in recorder.messages] == ["ahead"]


@pytest.mark.asyncio
async def test_the_seen_set_stays_bounded():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    for i in range(1, 6):
        await _insert(id=i * 1000)
        await _poll(position, recorder)
    assert len(position.seen) <= 2


@pytest.mark.asyncio
async def test_a_batch_limit_carries_over_to_the_next_poll(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EVENT_BATCH_SIZE", 3)
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    for i in range(7):
        await _insert(aggregate_id=str(i))
    for _ in range(4):
        await _poll(position, recorder)
    assert [m["aggregate_id"] for m in recorder.messages] == [str(i) for i in range(7)]


# ── what is delivered, and what cannot stop it ────────────────────────────────


@pytest.mark.asyncio
async def test_the_message_carries_the_event_and_its_payload():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert("ticket.closed", "ticket", "7", json.dumps({"ticket_id": 7}))
    summary = await _poll(position, recorder)
    assert recorder.messages == [
        {"type": "ticket.closed", "aggregate_type": "ticket", "aggregate_id": "7", "payload": {"ticket_id": 7}}
    ]
    assert summary["delivered"] == 1


@pytest.mark.asyncio
async def test_an_unknown_event_type_is_processed_without_error():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert("galaxy.exploded", "galaxy", "42", '{"foo": "bar"}')
    assert (await _poll(position, recorder))["processed"] == 1


@pytest.mark.asyncio
async def test_a_poison_payload_is_counted_and_skipped_without_blocking_the_next_event():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert(aggregate_id="bad", payload="NOT VALID JSON")
    await _insert(aggregate_id="good")
    summary = await _poll(position, recorder)
    assert summary["failed"] == 1 and summary["processed"] == 1
    assert [m["aggregate_id"] for m in recorder.messages] == ["good"]


@pytest.mark.asyncio
async def test_a_failing_handler_does_not_stop_the_others_or_the_delivery():
    calls = []

    async def broken(payload, session):
        raise RuntimeError("boom")

    async def working(payload, session):
        calls.append(payload)

    registry.register("ticket.created", broken)
    registry.register("ticket.created", working)
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert()
    summary = await _poll(position, recorder)
    assert calls == [{"a": 1}]
    assert len(recorder.messages) == 1 and summary["processed"] == 1


@pytest.mark.asyncio
async def test_a_failing_delivery_does_not_stop_the_poll():
    async def broken_delivery(session, message):
        raise RuntimeError("socket exploded")

    position = EventCursor()
    await poll_and_process_events(TestingSessionLocal, position=position, deliver=broken_delivery)
    await _insert(aggregate_id="1")
    await _insert(aggregate_id="2")
    summary = await poll_and_process_events(TestingSessionLocal, position=position, deliver=broken_delivery)
    assert summary["processed"] == 2                            # both were read; neither blocked the other


# ── purge ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_deletes_outbox_events_by_age_whatever_their_status():
    old = _utcnow() - timedelta(days=35)
    await _insert(aggregate_id="old-pending", created_at=old, status="pending")
    await _insert(aggregate_id="old-processed", created_at=old, status="processed")
    await _insert(aggregate_id="fresh", status="pending")

    assert await purge_old_events(TestingSessionLocal, ttl_days=30) == 2

    async with TestingSessionLocal() as session:
        remaining = (await session.execute(select(IntegrationOutbox))).scalars().all()
    assert [r.aggregate_id for r in remaining] == ["fresh"]


@pytest.mark.asyncio
async def test_purge_also_clears_finished_inbox_rows_but_never_pending_ones():
    old_time = _utcnow() - timedelta(days=35)
    async with TestingSessionLocal() as session:
        session.add_all([
            IntegrationInbox(command_type="ticket.close", aggregate_type="ticket", aggregate_id="1", status="done", created_at=old_time),
            IntegrationInbox(command_type="ticket.close", aggregate_type="ticket", aggregate_id="2", status="failed", created_at=old_time),
            IntegrationInbox(command_type="ticket.close", aggregate_type="ticket", aggregate_id="3", status="pending", created_at=old_time),
            IntegrationInbox(command_type="ticket.close", aggregate_type="ticket", aggregate_id="4", status="done", created_at=_utcnow()),
        ])
        await session.commit()

    await purge_old_events(TestingSessionLocal, ttl_days=30)

    async with TestingSessionLocal() as session:
        remaining = (await session.execute(select(IntegrationInbox))).scalars().all()
    assert sorted(r.aggregate_id for r in remaining) == ["3", "4"]


@pytest.mark.asyncio
async def test_a_creates_message_carries_the_id_from_its_result():
    position, recorder = EventCursor(), Recorder()
    await _poll(position, recorder)
    await _insert("ticket.created", "ticket", "", json.dumps({"ticket_id": 31}))
    await _poll(position, recorder)
    assert recorder.messages[0]["aggregate_id"] == "31"
