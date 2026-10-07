"""Ticket filters — an Admin Panel page (``scp/filters.php``, ``isAdmin()``): every route is ``require_admin()``. The
system ban list is a filter too, but it has its own page and API (``/banlist``) and is left out here."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.filters import service
from app.modules.filters.models import CommandResponse, FilterCreate, FilterDetail, FilterList, FilterOptions, FilterUpdate

router = APIRouter(tags=["ticket filters"])

Key = Header(default=None, alias="Idempotency-Key")


@router.get("/filters", response_model=FilterList, summary="List ticket filters")
async def list_filters(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_filters(session, page=page, page_size=page_size)


@router.get("/filters/options", response_model=FilterOptions, summary="What a filter form offers")
async def filter_options(current_user: dict = require_admin()):
    return await service.options(current_user["staff_id"])


@router.get("/filters/{filter_id}", response_model=FilterDetail, summary="A ticket filter with its rules and actions")
async def get_filter(
    filter_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_filter(session, filter_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Filter not found")
    return found


@router.post("/filters", response_model=CommandResponse, status_code=202, summary="Create a ticket filter")
async def create_filter(
    body: FilterCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_filter(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/filters/{filter_id}", response_model=CommandResponse, status_code=202, summary="Update a ticket filter")
async def update_filter(
    filter_id: int,
    body: FilterUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_filter(session, filter_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/filters/{filter_id}", response_model=CommandResponse, status_code=202, summary="Delete a ticket filter")
async def delete_filter(
    filter_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_filter(session, filter_id, current_user["staff_id"], idempotency_key)
