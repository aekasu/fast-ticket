"""User and Organization endpoints.

Permissions (global permissions on the staff member — ``Staff::hasPerm($key)``):
  reads          user.dir      the user/organization directory (osTicket does not gate organization
                               pages by URL; the API uses the directory permission as the nearest gate)
  users          user.create   user.edit   user.delete
  accounts       user.manage   (register, change/lock/unlock, password-reset and activation emails — osTicket's
                               "Manage Account" pop-ups)
  organizations  org.create    org.edit    org.delete
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.users import service
from app.modules.users.models import (
    CommandResponse,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationDetail,
    OrganizationListResponse,
    UserAccountRegister,
    UserAccountUpdate,
    UserCreate,
    UserUpdate,
    UserDetail,
    UserListResponse,
)

router = APIRouter(tags=["users"])

# ── Organizations ─────────────────────────────────────────────────────────────

@router.get("/orgs", response_model=OrganizationListResponse, summary="List organizations")
async def list_organizations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("user.dir"), # any org perm is fine
    session: AsyncSession = Depends(get_session),
):
    return await service.list_organizations(session, page=page, page_size=page_size)


@router.get("/orgs/{org_id}", response_model=OrganizationDetail, summary="Get organization")
async def get_organization(
    org_id: int,
    current_user: dict = require_perm("user.dir"),
    session: AsyncSession = Depends(get_session),
):
    org = await service.get_organization(session, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.post("/orgs", response_model=CommandResponse, status_code=202, summary="Create organization")
async def create_organization(
    body: OrganizationCreate,
    current_user: dict = require_perm("org.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_organization(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/orgs/{org_id}", response_model=CommandResponse, status_code=202, summary="Update organization")
async def update_organization(
    org_id: int,
    body: OrganizationUpdate,
    current_user: dict = require_perm("org.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_organization(session, org_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/orgs/{org_id}", response_model=CommandResponse, status_code=202, summary="Delete organization")
async def delete_organization(
    org_id: int,
    current_user: dict = require_perm("org.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_organization(session, org_id, current_user["staff_id"], idempotency_key)


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=UserListResponse, summary="List users")
async def list_users(
    org_id: int | None = Query(default=None, description="Filter by organization ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("user.dir"), # any user perm is fine
    session: AsyncSession = Depends(get_session),
):
    return await service.list_users(session, org_id=org_id, page=page, page_size=page_size)


@router.get("/users/{user_id}", response_model=UserDetail, summary="Get user")
async def get_user(
    user_id: int,
    current_user: dict = require_perm("user.dir"),
    session: AsyncSession = Depends(get_session),
):
    user = await service.get_user(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/users", response_model=CommandResponse, status_code=202, summary="Create user")
async def create_user(
    body: UserCreate,
    current_user: dict = require_perm("user.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_user(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/users/{user_id}", response_model=CommandResponse, status_code=202, summary="Update user")
async def update_user(
    user_id: int,
    body: UserUpdate,
    current_user: dict = require_perm("user.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_user(session, user_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/users/{user_id}", response_model=CommandResponse, status_code=202, summary="Delete user")
async def delete_user(
    user_id: int,
    current_user: dict = require_perm("user.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_user(session, user_id, current_user["staff_id"], idempotency_key)


# ── End-user accounts (user.manage, as osTicket's "Manage Account") ───────────────

@router.post("/users/{user_id}/account", response_model=CommandResponse, status_code=202,
             summary="Register a login account for a user")
async def register_account(
    user_id: int,
    body: UserAccountRegister,
    current_user: dict = require_perm("user.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.register_account(session, user_id, body, current_user["staff_id"], idempotency_key)


@router.patch("/users/{user_id}/account", response_model=CommandResponse, status_code=202,
              summary="Change a user's account: username, timezone, password, lock, password-reset flags")
async def update_account(
    user_id: int,
    body: UserAccountUpdate,
    current_user: dict = require_perm("user.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_account(session, user_id, body, current_user["staff_id"], idempotency_key)


@router.post("/users/{user_id}/account/password-reset", response_model=CommandResponse, status_code=202,
             summary="Email the user a password-reset link")
async def send_password_reset(
    user_id: int,
    current_user: dict = require_perm("user.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.send_password_reset(session, user_id, current_user["staff_id"], idempotency_key)


@router.post("/users/{user_id}/account/confirmation", response_model=CommandResponse, status_code=202,
             summary="Email the user an account-activation link")
async def send_confirmation(
    user_id: int,
    current_user: dict = require_perm("user.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.send_confirmation(session, user_id, current_user["staff_id"], idempotency_key)
