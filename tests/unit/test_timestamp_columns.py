"""Inbox/outbox timestamps are naive UTC.

They live in MySQL ``DATETIME`` columns that the PHP bridge writes with ``NOW()``.
sqlmodel>=0.0.47 wraps plain ``datetime`` columns in a type that rejects naive values,
which made every API write and every outbox poll fail with a 500 on MySQL. The fix is an
explicit ``sa_type``; these tests pin it so the models can't quietly lose it.

Note the suite only catches a regression on a sqlmodel that has the strict wrapper. Run
``make test-docker`` (the image's dependency versions) as well as ``make test``.
"""

from datetime import datetime

import pytest
from sqlalchemy import DateTime

from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox
from tests.conftest import TestingSessionLocal

TIMESTAMP_COLUMNS = [
    (IntegrationInbox, "created_at"),
    (IntegrationInbox, "processed_at"),
    (IntegrationOutbox, "created_at"),
    (IntegrationOutbox, "locked_at"),
    (IntegrationOutbox, "processed_at"),
]


@pytest.mark.parametrize(("model", "column"), TIMESTAMP_COLUMNS)
def test_timestamp_column_is_a_plain_naive_datetime(model, column):
    col_type = model.__table__.c[column].type
    assert type(col_type) is DateTime, f"{model.__name__}.{column} uses {type(col_type)!r}"
    assert col_type.timezone is False


@pytest.mark.asyncio
async def test_default_created_at_is_naive_and_round_trips():
    async with TestingSessionLocal() as session:
        row = IntegrationInbox(command_type="ticket.close", aggregate_type="ticket", aggregate_id="1")
        assert row.created_at.tzinfo is None
        session.add(row)
        await session.commit()
        await session.refresh(row)
        stored = row.created_at

    assert isinstance(stored, datetime)
    assert stored.tzinfo is None


@pytest.mark.asyncio
async def test_outbox_accepts_naive_processing_timestamps():
    naive = datetime(2026, 9, 29, 15, 0, 0)
    async with TestingSessionLocal() as session:
        ev = IntegrationOutbox(
            event_type="kb.canned.created", aggregate_type="canned", locked_at=naive, processed_at=naive
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)

    assert ev.locked_at == naive
    assert ev.processed_at == naive
