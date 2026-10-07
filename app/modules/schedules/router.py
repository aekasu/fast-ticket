"""Business-hours and holiday schedules — an Admin Panel page (``scp/schedules.php``, ``isAdmin()``): every route is
``require_admin()``."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.schedules import service
from app.modules.schedules.models import (
    CommandResponse, EntryCreate, EntryUpdate, ScheduleCreate, ScheduleDetail, ScheduleList, ScheduleUpdate)

router = APIRouter(tags=["schedules"])

Key = Header(default=None, alias="Idempotency-Key")


@router.get("/schedules", response_model=ScheduleList, summary="List schedules")
async def list_schedules(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_schedules(session, page=page, page_size=page_size)


@router.get("/schedules/{schedule_id}", response_model=ScheduleDetail, summary="A schedule with its entries")
async def get_schedule(
    schedule_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_schedule(session, schedule_id, current_user["staff_id"])
    if found is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return found


@router.post("/schedules", response_model=CommandResponse, status_code=202, summary="Create (or copy) a schedule")
async def create_schedule(
    body: ScheduleCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_schedule(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/schedules/{schedule_id}", response_model=CommandResponse, status_code=202, summary="Update a schedule")
async def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_schedule(session, schedule_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/schedules/{schedule_id}", response_model=CommandResponse, status_code=202, summary="Delete a schedule")
async def delete_schedule(
    schedule_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_schedule(session, schedule_id, current_user["staff_id"], idempotency_key)


@router.post("/schedules/{schedule_id}/entries", response_model=CommandResponse, status_code=202, summary="Add an entry")
async def create_entry(
    schedule_id: int,
    body: EntryCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_entry(session, schedule_id, body, current_user["staff_id"], idempotency_key)


@router.put("/schedules/{schedule_id}/entries/{entry_id}", response_model=CommandResponse, status_code=202, summary="Replace an entry")
async def update_entry(
    schedule_id: int,
    entry_id: int,
    body: EntryUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_entry(session, schedule_id, entry_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/schedules/{schedule_id}/entries/{entry_id}", response_model=CommandResponse, status_code=202, summary="Delete an entry")
async def delete_entry(
    schedule_id: int,
    entry_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_entry(session, schedule_id, entry_id, current_user["staff_id"], idempotency_key)
