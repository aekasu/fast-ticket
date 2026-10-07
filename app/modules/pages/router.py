"""Site pages (landing, offline, thank-you and other) — an Admin Panel page (``scp/pages.php``, ``isAdmin()``): every
route is ``require_admin()``."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.pages import service
from app.modules.pages.models import CommandResponse, PageCreate, PageDetail, PageList, PageUpdate

router = APIRouter(tags=["pages"])


@router.get("/pages", response_model=PageList, summary="List site pages")
async def list_pages(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_pages(session, page=page, page_size=page_size)


@router.get("/pages/{page_id}", response_model=PageDetail, summary="Get a site page")
async def get_page(
    page_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_page(session, page_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return found


@router.post("/pages", response_model=CommandResponse, status_code=202, summary="Create a site page")
async def create_page(
    body: PageCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_page(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/pages/{page_id}", response_model=CommandResponse, status_code=202, summary="Update a site page")
async def update_page(
    page_id: int,
    body: PageUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_page(session, page_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/pages/{page_id}", response_model=CommandResponse, status_code=202, summary="Delete a site page (not while in use)")
async def delete_page(
    page_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_page(session, page_id, current_user["staff_id"], idempotency_key)
