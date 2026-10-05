from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import _perm_set
from app.core.permissions import GLOBAL_PERMISSIONS, ROLE_PERMISSIONS
from app.integration.osticket import read_repository as repo
from app.modules.roles.models import PermissionKey, PermissionKeyList, Role, RoleListResponse


async def list_roles(session: AsyncSession, *, page: int, page_size: int) -> RoleListResponse:
    rows = await repo.list_roles(session, limit=page_size, offset=(page - 1) * page_size)
    return RoleListResponse(
        items=[
            Role(id=r["id"], name=r["name"] or "", notes=r["notes"] or "", enabled=bool(r["enabled"]),
                 permissions=sorted(_perm_set(r["permissions"]) & ROLE_PERMISSIONS))
            for r in map(dict, rows)
        ],
        page=page,
        page_size=page_size,
    )


def permission_keys() -> PermissionKeyList:
    keys = [PermissionKey(key=k, kind="global") for k in sorted(GLOBAL_PERMISSIONS)]
    keys += [PermissionKey(key=k, kind="role") for k in sorted(ROLE_PERMISSIONS)]
    return PermissionKeyList(permissions=keys)
