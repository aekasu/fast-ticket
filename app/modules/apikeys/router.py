"""osTicket's API keys — an Admin Panel page (``scp/apikeys.php``, ``isAdmin()``): every route is ``require_admin()``."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.apikeys import service
from app.modules.apikeys.models import ApiKey, ApiKeyCreate, ApiKeyList, ApiKeyUpdate, CommandResponse

router = APIRouter(tags=["api keys"])


@router.get("/api-keys", response_model=ApiKeyList, summary="List osTicket's API keys")
async def list_api_keys(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_api_keys(session, page=page, page_size=page_size)


@router.get("/api-keys/{key_id}", response_model=ApiKey, summary="Get an API key")
async def get_api_key(
    key_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    key = await service.get_api_key(session, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return key


@router.post("/api-keys", response_model=CommandResponse, status_code=202, summary="Create an API key")
async def create_api_key(
    body: ApiKeyCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_api_key(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/api-keys/{key_id}", response_model=CommandResponse, status_code=202, summary="Update an API key")
async def update_api_key(
    key_id: int,
    body: ApiKeyUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_api_key(session, key_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/api-keys/{key_id}", response_model=CommandResponse, status_code=202, summary="Delete an API key")
async def delete_api_key(
    key_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_api_key(session, key_id, current_user["staff_id"], idempotency_key)
