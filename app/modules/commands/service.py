"""Commands service layer."""

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
