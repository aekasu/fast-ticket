"""Banlist endpoints.

Permissions — the banlist page is in the Admin Panel (isAdmin()): admin-only. (``emails.banlist`` is
osTicket's separate "ban this sender" action on a ticket, not banlist management.)
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.banlist import service
from app.modules.banlist.models import (
    CommandResponse,
    BanlistCreate,
    BanlistUpdate,
    BanlistDetail,
    BanlistListResponse,
)

router = APIRouter(tags=["banlist"])

@router.get("/banlist", response_model=BanlistListResponse, summary="List banlist entries")
async def list_banlist(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_banlist(session, page=page, page_size=page_size)

@router.get("/banlist/{ban_id}", response_model=BanlistDetail, summary="Get banlist entry")
async def get_banlist_entry(
    ban_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    entry = await service.get_banlist_entry(session, ban_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Banlist entry not found")
    return entry

@router.post("/banlist", response_model=CommandResponse, status_code=202, summary="Create banlist entry")
async def create_banlist_entry(
    body: BanlistCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_banlist_entry(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/banlist/{ban_id}", response_model=CommandResponse, status_code=202, summary="Update banlist entry")
async def update_banlist_entry(
    ban_id: int,
    body: BanlistUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_banlist_entry(session, ban_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/banlist/{ban_id}", response_model=CommandResponse, status_code=202, summary="Delete banlist entry")
async def delete_banlist_entry(
    ban_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_banlist_entry(session, ban_id, current_user["staff_id"], idempotency_key)
