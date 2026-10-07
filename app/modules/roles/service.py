from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import _perm_set
from app.core.permissions import GLOBAL_PERMISSIONS, ROLE_PERMISSIONS
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.roles.models import (
    CommandResponse, PermissionKey, PermissionKeyList, Role, RoleCreate, RoleListResponse, RoleUpdate)


def _role(r: dict) -> Role:
    return Role(id=r["id"], name=r["name"] or "", notes=r["notes"] or "", enabled=bool(r["enabled"]),
                permissions=sorted(_perm_set(r["permissions"]) & ROLE_PERMISSIONS),
                created=r.get("created"), updated=r.get("updated"), in_use=r.get("in_use"))


async def list_roles(session: AsyncSession, *, page: int, page_size: int) -> RoleListResponse:
    rows = await repo.list_roles(session, limit=page_size, offset=(page - 1) * page_size)
    return RoleListResponse(items=[_role(dict(r)) for r in rows], page=page, page_size=page_size)


async def get_role(session: AsyncSession, role_id: int) -> Role | None:
    row = await repo.get_role(session, role_id)
    return _role(row) if row else None


def permission_keys() -> PermissionKeyList:
    keys = [PermissionKey(key=k, kind="global") for k in sorted(GLOBAL_PERMISSIONS)]
    keys += [PermissionKey(key=k, kind="role") for k in sorted(ROLE_PERMISSIONS)]
    return PermissionKeyList(permissions=keys)


def _response(cmd, idempotency_key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def create_role(session: AsyncSession, body: RoleCreate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="role.create", aggregate_type="role", aggregate_id="",
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def update_role(session: AsyncSession, role_id: int, body: RoleUpdate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="role.update", aggregate_type="role", aggregate_id=str(role_id),
        payload={"role_id": role_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def delete_role(session: AsyncSession, role_id: int, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="role.delete", aggregate_type="role", aggregate_id=str(role_id),
        payload={"role_id": role_id}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)
