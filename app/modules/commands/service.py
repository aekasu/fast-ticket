"""Commands service layer."""

import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.modules.commands.models import (
    CommandListItem,
    CommandDetail,
    CommandListResponse,
)

logger = logging.getLogger(__name__)


async def list_commands(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> CommandListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_commands(session, limit=page_size, offset=offset)
    
    items = [
        CommandListItem(
            id=row["id"],
            command_type=row["command_type"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            status=row["status"],
            requested_by_staff_id=row["requested_by_staff_id"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )
        for row in rows
    ]
    
    return CommandListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_command(session: AsyncSession, command_id: int) -> CommandDetail | None:
    row = await repo.get_command(session, command_id)
    if not row:
        return None
    return CommandDetail(**row)


FINISHED_STATUSES = ("done", "failed")
_WAIT_POLL_SECONDS = 0.25


async def wait_for_command(
    session: AsyncSession, command_id: int, wait: float
) -> CommandDetail | None:
    """Return the command, holding the request up to ``wait`` seconds for it to finish.

    Lets a client learn the result of a write (and the id a create produced) in one call
    instead of polling. Returns the row as it stands when the time is up, still
    ``pending``/``processing``, so the caller can keep waiting.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while True:
        command = await get_command(session, command_id)
        if command is None or command.status in FINISHED_STATUSES or loop.time() >= deadline:
            return command
        # End the read transaction: under MySQL's REPEATABLE READ the next SELECT would
        # otherwise keep seeing the snapshot from before the bridge committed its result.
        await session.rollback()
        await asyncio.sleep(_WAIT_POLL_SECONDS)
