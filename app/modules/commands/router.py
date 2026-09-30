"""Commands endpoints.

Permissions — any enabled agent, scoped: you see the commands *you* queued (which is how you learn the result of
a write: ``GET /commands/{id}?wait=``); administrators see everyone's. Secrets in a payload (a staff member's
password) are masked. The command inbox is this API's own plumbing, not an osTicket object, so osTicket has no
rule for it; "your own" is the natural one.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_staff
from app.modules.commands import service
from app.modules.commands.models import (
    CommandDetail,
    CommandListResponse,
)

router = APIRouter(tags=["commands"])

@router.get("/commands", response_model=CommandListResponse, summary="List integration commands")
async def list_commands(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_commands(
        session, page=page, page_size=page_size, access=current_user["access"]
    )

@router.get("/commands/{command_id}", response_model=CommandDetail, summary="Get command detail")
async def get_command(
    command_id: int,
    wait: float = Query(
        default=0,
        ge=0,
        le=30,
        description="Seconds to hold the request while the command is still pending/processing.",
    ),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    command = await service.wait_for_command(
        session, command_id, wait, access=current_user["access"]
    )
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    return command
