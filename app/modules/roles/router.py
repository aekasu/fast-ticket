"""Roles and permission keys.

Permissions — roles are an Admin Panel page (``scp/roles.php``, ``isAdmin()``), so the list is
``require_admin()``; it exists so a client can offer a role when creating an agent or giving department
access. Roles cannot be created or edited through the API yet. The permission keys are fixed data
(``app/core/permissions.py``), open to any agent.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin, require_staff
from app.modules.roles import service
from app.modules.roles.models import PermissionKeyList, RoleListResponse

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
