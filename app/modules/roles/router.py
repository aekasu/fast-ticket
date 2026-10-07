"""Roles and permission keys.

Permissions — roles are an Admin Panel page (``scp/roles.php``, ``isAdmin()``), so every role route is
``require_admin()``; the list also lets a client offer a role when creating an agent or giving department
access. The permission keys are fixed data (``app/core/permissions.py``), open to any agent.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin, require_staff
from app.modules.roles import service
from app.modules.roles.models import CommandResponse, PermissionKeyList, Role, RoleCreate, RoleListResponse, RoleUpdate

router = APIRouter(tags=["roles"])


@router.get("/roles", response_model=RoleListResponse, summary="List roles")
async def list_roles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_roles(session, page=page, page_size=page_size)


@router.get("/permissions", response_model=PermissionKeyList, summary="osTicket's permission keys and where each lives")
async def list_permission_keys(current_user: dict = require_staff()):
    return service.permission_keys()


@router.get("/roles/{role_id}", response_model=Role, summary="Get a role")
async def get_role(
    role_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    role = await service.get_role(session, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.post("/roles", response_model=CommandResponse, status_code=202, summary="Create a role")
async def create_role(
    body: RoleCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_role(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/roles/{role_id}", response_model=CommandResponse, status_code=202, summary="Update a role")
async def update_role(
    role_id: int,
    body: RoleUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_role(session, role_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/roles/{role_id}", response_model=CommandResponse, status_code=202, summary="Delete a role (not while in use)")
async def delete_role(
    role_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_role(session, role_id, current_user["staff_id"], idempotency_key)
