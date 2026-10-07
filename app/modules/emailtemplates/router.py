"""Email template sets and their messages — an Admin Panel page (``scp/templates.php``, ``isAdmin()``): every route is
``require_admin()``."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.emailtemplates import service
from app.modules.emailtemplates.models import (
    CommandResponse, MessageBody, MessageUpdate, TemplateSetCreate, TemplateSetDetail, TemplateSetList, TemplateSetUpdate)

router = APIRouter(tags=["email templates"])

Key = Header(default=None, alias="Idempotency-Key")


@router.get("/email-templates", response_model=TemplateSetList, summary="List email template sets")
async def list_sets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_sets(session, page=page, page_size=page_size)


@router.get("/email-templates/{set_id}", response_model=TemplateSetDetail, summary="A template set and its messages")
async def get_set(
    set_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_set(session, set_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Template set not found")
    return found


@router.get("/email-templates/{set_id}/messages/{code_name}", response_model=MessageBody, summary="One message with its text")
async def get_message(
    set_id: int,
    code_name: str,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_message(session, set_id, code_name)
    if found is None:
        raise HTTPException(status_code=404, detail="Message template not found")
    return found


@router.post("/email-templates", response_model=CommandResponse, status_code=202, summary="Create a template set (optionally a copy)")
async def create_set(
    body: TemplateSetCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_set(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/email-templates/{set_id}", response_model=CommandResponse, status_code=202, summary="Update a template set")
async def update_set(
    set_id: int,
    body: TemplateSetUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_set(session, set_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/email-templates/{set_id}", response_model=CommandResponse, status_code=202, summary="Delete a template set (not while in use)")
async def delete_set(
    set_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_set(session, set_id, current_user["staff_id"], idempotency_key)


@router.put("/email-templates/{set_id}/messages/{code_name}", response_model=CommandResponse, status_code=202,
            summary="Set a message's subject and body (adds the message to the set if it has no copy yet)")
async def update_message(
    set_id: int,
    code_name: str,
    body: MessageUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    if code_name not in service.NAMES:
        raise HTTPException(status_code=404, detail="Message template not found")
    return await service.update_message(session, set_id, code_name, body, current_user["staff_id"], idempotency_key)
