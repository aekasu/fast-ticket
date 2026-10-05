"""Forms endpoints.

Permissions — managing Dynamic Forms is the Admin Panel (isAdmin()): admin-only. The one read open to any agent is
``GET /forms/for/{kind}``: the fields of the ticket, user, organization or task form as osTicket's own create and
edit pages show them to staff (enabled and agent-visible), which an agent needs to type custom answers.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from typing import Literal

from app.core.security import require_admin, require_staff
from app.modules.forms import service
from app.modules.forms.models import (
    CommandResponse,
    FormCreate,
    FormFieldCreate,
    FormFieldUpdate,
    FormUpdate,
    FormDetail,
    FormListResponse,
    AgentForms,
)

router = APIRouter(tags=["forms"])

@router.get(
    "/forms/for/{kind}",
    response_model=AgentForms,
    summary="The form fields an agent fills in for a ticket, user, organization or task",
)
async def forms_for(
    kind: Literal["ticket", "user", "organization", "task"],
    topic_id: int | None = Query(default=None, gt=0, description="Tickets only: also the help topic's forms"),
    current_user: dict = require_staff(),   # osTicket shows these on pages gated by the object's own permissions
    session: AsyncSession = Depends(get_session),
):
    """Enabled, agent-visible fields in osTicket's order, with whether each is editable and required for agents."""
    return await service.object_forms(session, kind, topic_id)


@router.get("/forms", response_model=FormListResponse, summary="List forms")
async def list_forms(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_forms(session, page=page, page_size=page_size)

@router.get("/forms/{form_id}", response_model=FormDetail, summary="Get form")
async def get_form(
    form_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    form = await service.get_form(session, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return form

@router.post("/forms", response_model=CommandResponse, status_code=202, summary="Create form")
async def create_form(
    body: FormCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_form(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/forms/{form_id}", response_model=CommandResponse, status_code=202, summary="Update form")
async def update_form(
    form_id: int,
    body: FormUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_form(session, form_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/forms/{form_id}", response_model=CommandResponse, status_code=202, summary="Delete form")
async def delete_form(
    form_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_form(session, form_id, current_user["staff_id"], idempotency_key)


@router.post("/forms/{form_id}/fields", response_model=CommandResponse, status_code=202, summary="Add a field to a form")
async def add_field(
    form_id: int,
    body: FormFieldCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_field(session, form_id, body, current_user["staff_id"], idempotency_key)


@router.patch("/forms/{form_id}/fields/{field_id}", response_model=CommandResponse, status_code=202,
              summary="Change a form field: label, name, type, mode, order, hint, enabled, configuration")
async def update_field(
    form_id: int,
    field_id: int,
    body: FormFieldUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_field(session, form_id, field_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/forms/{form_id}/fields/{field_id}", response_model=CommandResponse, status_code=202,
               summary="Remove a field from a form (its answers are kept unless delete_data)")
async def delete_field(
    form_id: int,
    field_id: int,
    delete_data: bool = Query(default=False, description="Also delete every answer already given to this field."),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_field(session, form_id, field_id, delete_data, current_user["staff_id"], idempotency_key)
