"""The system's email addresses — an Admin Panel page (``scp/emails.php``, ``isAdmin()``): every route is
``require_admin()``. The mail accounts of an address (mailbox fetching, SMTP) are shown without credentials and are
set up in osTicket (they involve passwords, OAuth and connection tests)."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.emails import service
from app.modules.emails.models import CommandResponse, EmailAddressDetail, EmailAddressList, EmailCreate, EmailUpdate

router = APIRouter(tags=["emails"])

Key = Header(default=None, alias="Idempotency-Key")


@router.get("/emails", response_model=EmailAddressList, summary="The system's email addresses")
async def list_emails(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_emails(session, page=page, page_size=page_size)


@router.get("/emails/{email_id}", response_model=EmailAddressDetail, summary="An email address with its mail accounts (no credentials)")
async def get_email(
    email_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_email(session, email_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Email not found")
    return found


@router.post("/emails", response_model=CommandResponse, status_code=202, summary="Add an email address")
async def create_email(
    body: EmailCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.create_email(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/emails/{email_id}", response_model=CommandResponse, status_code=202, summary="Update an email address")
async def update_email(
    email_id: int,
    body: EmailUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.update_email(session, email_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/emails/{email_id}", response_model=CommandResponse, status_code=202, summary="Delete an email address (not the default or alert one)")
async def delete_email(
    email_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Key,
):
    return await service.delete_email(session, email_id, current_user["staff_id"], idempotency_key)
