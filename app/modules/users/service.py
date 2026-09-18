"""User and Organization service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.users.models import (
    CommandResponse,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationListItem,
    OrganizationDetail,
    OrganizationListResponse,
    UserCreate,
    UserUpdate,
    UserListItem,
    UserDetail,
    UserListResponse,
)

logger = logging.getLogger(__name__)


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Organizations ─────────────────────────────────────────────────────────────

async def list_organizations(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> OrganizationListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_organizations(session, limit=page_size, offset=offset)
    
    items = [
        OrganizationListItem(
            id=row["id"],
            name=row["name"],
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return OrganizationListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_organization(session: AsyncSession, org_id: int) -> OrganizationDetail | None:
    row = await repo.get_organization(session, org_id)
    if not row:
        return None
    return OrganizationDetail(**row)


async def create_organization(
    session: AsyncSession,
    body: OrganizationCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="org.create",
        aggregate_type="org",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_organization(
    session: AsyncSession,
    org_id: int,
    body: OrganizationUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="org.update",
        aggregate_type="org",
        aggregate_id=str(org_id),
        payload={"org_id": org_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_organization(
    session: AsyncSession,
    org_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="org.delete",
        aggregate_type="org",
        aggregate_id=str(org_id),
        payload={"org_id": org_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── Users ─────────────────────────────────────────────────────────────────────

async def list_users(
    session: AsyncSession,
    org_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> UserListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_users(session, org_id=org_id, limit=page_size, offset=offset)
    
    items = [
        UserListItem(
            id=row["id"],
            name=row["name"],
            email=row["email"] or "",
            org_id=row["org_id"],
            org_name=row["org_name"],
            status=row["status"],
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return UserListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_user(session: AsyncSession, user_id: int) -> UserDetail | None:
    row = await repo.get_user(session, user_id)
    if not row:
        return None
    # Fix potential None values for strings
    row_dict = dict(row)
    if row_dict.get("email") is None:
        row_dict["email"] = ""
    return UserDetail(**row_dict)

async def create_user(
    session: AsyncSession,
    body: UserCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="user.create",
        aggregate_type="user",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_user(
    session: AsyncSession,
    user_id: int,
    body: UserUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="user.update",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_user(
    session: AsyncSession,
    user_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="user.delete",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
