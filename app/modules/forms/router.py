"""Forms endpoints.

Permissions:
  form.manage
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.forms import service
from app.modules.forms.models import (
    CommandResponse,
    FormCreate,
    FormUpdate,
    FormDetail,
    FormListResponse,
)

router = APIRouter(tags=["forms"])

@router.get("/forms", response_model=FormListResponse, summary="List forms")
async def list_forms(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("form.manage"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_forms(session, page=page, page_size=page_size)

@router.get("/forms/{form_id}", response_model=FormDetail, summary="Get form")
async def get_form(
    form_id: int,
    current_user: dict = require_perm("form.manage"),
    session: AsyncSession = Depends(get_session),
):
    form = await service.get_form(session, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return form

@router.post("/forms", response_model=CommandResponse, status_code=202, summary="Create form")
async def create_form(
    body: FormCreate,
    current_user: dict = require_perm("form.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_form(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/forms/{form_id}", response_model=CommandResponse, status_code=202, summary="Update form")
async def update_form(
    form_id: int,
    body: FormUpdate,
    current_user: dict = require_perm("form.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_form(session, form_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/forms/{form_id}", response_model=CommandResponse, status_code=202, summary="Delete form")
async def delete_form(
    form_id: int,
    current_user: dict = require_perm("form.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_form(session, form_id, current_user["staff_id"], idempotency_key)
