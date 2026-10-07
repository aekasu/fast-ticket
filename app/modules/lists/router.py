"""Custom lists — an Admin Panel page (``scp/lists.php``, ``isAdmin()``): every route is ``require_admin()``.
Lists osTicket manages itself (ticket statuses) are shown read-only."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.lists import service
from app.modules.lists.models import (
    CommandResponse, CustomListItem, CustomListResponse, EntryCreate, EntryUpdate, ListCreate, ListEntryResponse, ListUpdate)

router = APIRouter(tags=["custom lists"])

Key = Header(default=None, alias="Idempotency-Key")


@router.get("/lists", response_model=CustomListResponse, summary="List custom lists")
async def list_lists(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_lists(session, page=page, page_size=page_size)


@router.get("/lists/{list_id}", response_model=CustomListItem, summary="Get a custom list")
async def get_list(
    list_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_list(session, list_id)
    if found is None:
        raise HTTPException(status_code=404, detail="List not found")
    return found


@router.get("/lists/{list_id}/items", response_model=ListEntryResponse, summary="The items of a custom list")
async def list_entries(
    list_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.list_entries(session, list_id, page=page, page_size=page_size)
    if found is None:
        raise HTTPException(status_code=404, detail="List not found")
    return found


@router.post("/lists", response_model=CommandResponse, status_code=202, summary="Create a custom list")
async def create_list(
    body: ListCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_list(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/lists/{list_id}", response_model=CommandResponse, status_code=202, summary="Update a custom list")
async def update_list(
    list_id: int,
    body: ListUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_list(session, list_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/lists/{list_id}", response_model=CommandResponse, status_code=202, summary="Delete a custom list (not while a field uses it)")
async def delete_list(
    list_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_list(session, list_id, current_user["staff_id"], idempotency_key)


@router.post("/lists/{list_id}/items", response_model=CommandResponse, status_code=202, summary="Add an item to a custom list")
async def create_entry(
    list_id: int,
    body: EntryCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_entry(session, list_id, body, current_user["staff_id"], idempotency_key)


@router.patch("/lists/{list_id}/items/{item_id}", response_model=CommandResponse, status_code=202, summary="Update a list item")
async def update_entry(
    list_id: int,
    item_id: int,
    body: EntryUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_entry(session, list_id, item_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/lists/{list_id}/items/{item_id}", response_model=CommandResponse, status_code=202, summary="Remove a list item")
async def delete_entry(
    list_id: int,
    item_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_entry(session, list_id, item_id, current_user["staff_id"], idempotency_key)
