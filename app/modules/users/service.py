"""User and Organization service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_fields import BUILT_IN_FIELDS, CustomFieldAnswer, answer_from_row, attach_custom_fields
from app.core.scope import UNRESTRICTED
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.users.models import (
    UserAccountInfo,
    CommandResponse,
    UserAccountInfo,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationListItem,
    OrganizationDetail,
    OrganizationListResponse,
    UserCreate,
    UserImport,
    UserUpdate,
    UserListItem,
    UserDetail,
    UserListResponse,
)

logger = logging.getLogger(__name__)


def _custom_answers(rows, object_type: str) -> list[CustomFieldAnswer]:
    skip = BUILT_IN_FIELDS[object_type]
    return [
        answer_from_row(r)
        for r in map(dict, rows)
        if r["name"] not in skip
    ]


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
    q: str | None = None,
    sort: str = "name",
    descending: bool = False,
    field_names: list[str] | None = None,
) -> OrganizationListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_organizations(
        session, q=q, sort=sort, descending=descending, limit=page_size, offset=offset)
    items = [
        OrganizationListItem(
            id=row["id"], name=row["name"], user_count=int(row.get("user_count") or 0),
            created=row["created"], updated=row["updated"],
        )
        for row in rows
    ]
    if field_names:
        answers = await repo.get_list_custom_fields(session, access=UNRESTRICTED, object_type="O", object_ids=[i.id for i in items], names=field_names)
        attach_custom_fields(items, "id", answers, "O")
    return OrganizationListResponse(
        items=items,
        total=await repo.count_organizations(session, q=q),
        page=page,
        page_size=page_size,
    )

async def get_organization(session: AsyncSession, org_id: int) -> OrganizationDetail | None:
    row = await repo.get_organization(session, org_id)
    if not row:
        return None
    row = dict(row)
    manager = row.pop("manager", "") or ""         # 's<staff id>' or 't<team id>'
    status = int(row.pop("status", 0) or 0)        # Organization::COLLAB_* / ASSIGN_* / SHARE_* bits
    return OrganizationDetail(
        **row,
        manager_staff_id=int(manager[1:]) if manager[:1] == "s" and manager[1:].isdigit() else 0,
        manager_team_id=int(manager[1:]) if manager[:1] == "t" and manager[1:].isdigit() else 0,
        sharing="all_members" if status & 0x10 else ("primary_contacts" if status & 0x8 else "disabled"),
        collab_all_members=bool(status & 0x1),
        collab_primary_contacts=bool(status & 0x2),
        assign_account_manager=bool(status & 0x4),
        primary_contact_ids=await repo.get_organization_primary_contacts(session, org_id),
        custom_fields=_custom_answers(await repo.get_organization_custom_fields(session, org_id), "O"),
    )


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

def _account_status(account_id, flags) -> str:
    """osTicket's directory Status: Guest without an account, else UserAccountStatus (locked, pending, active)."""
    if not account_id:
        return "guest"
    flags = int(flags or 0)
    return "locked" if flags & 0x2 else ("active" if flags & 0x1 else "pending")


async def list_users(
    session: AsyncSession,
    org_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    sort: str = "name",
    descending: bool = False,
    field_names: list[str] | None = None,
) -> UserListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_users(
        session, org_id=org_id, q=q, sort=sort, descending=descending, limit=page_size, offset=offset)
    items = [
            UserListItem(
                id=row["id"],
                name=row["name"],
                email=row["email"] or "",
                org_id=row["org_id"],
                org_name=row["org_name"],
                status=row["status"],
                account_status=_account_status(row.get("account_id"), row.get("account_flags")),
                ticket_count=int(row.get("ticket_count") or 0),
                created=row["created"],
                updated=row["updated"],
            )
            for row in rows
    ]
    if field_names:
        answers = await repo.get_list_custom_fields(session, access=UNRESTRICTED, object_type="U", object_ids=[i.id for i in items], names=field_names)
        attach_custom_fields(items, "id", answers, "U")
    return UserListResponse(
        items=items,
        total=await repo.count_users(session, org_id=org_id, q=q),
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
    account = await repo.get_user_account(session, user_id)
    info = None
    if account:
        flags = int(account["status"] or 0)    # UserAccountStatus: CONFIRMED 1, LOCKED 2, REQUIRE_RESET 4, FORBID_RESET 8
        info = UserAccountInfo(
            username=account["username"], timezone=account["timezone"],
            status="locked" if flags & 0x2 else ("active" if flags & 0x1 else "pending"),
            is_confirmed=bool(flags & 0x1), is_locked=bool(flags & 0x2),
            require_password_reset=bool(flags & 0x4), forbid_password_reset=bool(flags & 0x8),
            registered=account["registered"],
        )
    return UserDetail(**row_dict, is_primary_contact=bool(int(row_dict.get("status") or 0) & 0x1), account=info,
                      custom_fields=_custom_answers(await repo.get_user_custom_fields(session, user_id), "U"))

async def import_users(
    session: AsyncSession, body: UserImport, staff_id: int, idempotency_key: str | None
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="user.import",
        aggregate_type="user",
        aggregate_id="",
        payload=body.model_dump(),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def register_account(session, user_id, body, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="user.account.register",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def update_account(session, user_id, body, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="user.account.update",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def send_password_reset(session, user_id, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="user.account.password_reset",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def send_confirmation(session, user_id, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="user.account.confirmation",
        aggregate_type="user",
        aggregate_id=str(user_id),
        payload={"user_id": user_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


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
