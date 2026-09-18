"""Banlist service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.banlist.models import (
    CommandResponse,
    BanlistCreate,
    BanlistUpdate,
    BanlistListItem,
    BanlistDetail,
    BanlistListResponse,
)

logger = logging.getLogger(__name__)


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Banlist ───────────────────────────────────────────────────────────────────

async def list_banlist(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> BanlistListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_banlist(session, limit=page_size, offset=offset)
    
    items = [
        BanlistListItem(
            id=row["id"],
            val=row["val"],
            is_active=bool(row["is_active"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return BanlistListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_banlist_entry(session: AsyncSession, ban_id: int) -> BanlistDetail | None:
    row = await repo.get_banlist_entry(session, ban_id)
    if not row:
        return None
    return BanlistDetail(**row)


async def create_banlist_entry(
    session: AsyncSession,
    body: BanlistCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="banlist.create",
        aggregate_type="banlist",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_banlist_entry(
    session: AsyncSession,
    ban_id: int,
    body: BanlistUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="banlist.update",
        aggregate_type="banlist",
        aggregate_id=str(ban_id),
        payload={"ban_id": ban_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_banlist_entry(
    session: AsyncSession,
    ban_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="banlist.delete",
        aggregate_type="banlist",
        aggregate_id=str(ban_id),
        payload={"ban_id": ban_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
