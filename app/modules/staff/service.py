"""Staff, Teams, and Departments service layer."""

import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import GLOBAL_PERMISSIONS
from app.core.scope import UNRESTRICTED
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.staff.models import (
    CommandResponse,
    DepartmentAccess,
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentListItem,
    DepartmentDetail,
    DepartmentMember,
    DepartmentListResponse,
    TeamCreate,
    TeamUpdate,
    TeamListItem,
    TeamDetail,
    TeamListResponse,
    StaffCreate,
    StaffUpdate,
    StaffListItem,
    StaffDetail,
    StaffListResponse,
)

logger = logging.getLogger(__name__)


def _json_object(raw) -> dict:
    """osTicket stores permission / extra maps as JSON text, NULL when unset."""
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Departments ───────────────────────────────────────────────────────────────

async def list_departments(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
    *,
    access,
) -> DepartmentListResponse:
    """Departments as the caller may see them: an administrator (the Admin Panel) or an agent with
    ``visibility.departments`` sees all; anyone else only their own (``Staff::getDepartmentNames``)."""
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    everything = access.is_admin or access.has_global("visibility.departments") or not access.dept_ids
    rows = await repo.list_departments(
        session, access=UNRESTRICTED if everything else access, limit=page_size, offset=offset
    )
    
    items = [
        DepartmentListItem(
            id=row["id"],
            name=row["name"],
            is_public=bool(row["is_public"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return DepartmentListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

# Dept::FLAG_* bits and the Dept::ALERTS_* modes (include/class.dept.php)
_ALERTS_BY_MODE = {0: "dept_only", 1: "dept_and_extended", 2: "no_one", 3: "admin_only"}


def _department_detail(row: dict, members: list[dict]) -> DepartmentDetail:
    flags = int(row.pop("flags", 0) or 0)
    mode = row.pop("group_membership", 0)
    ticket_auto = bool(row.pop("ticket_auto_response", 1))
    message_auto = bool(row.pop("message_auto_response", 0))
    return DepartmentDetail(
        **row,
        status="archived" if flags & 0x8 else ("active" if flags & 0x4 else "disabled"),
        assignment="members" if flags & 0x1 else ("primary" if flags & 0x10 else "all"),
        disable_auto_claim=bool(flags & 0x2),
        disable_reopen_auto_assign=bool(flags & 0x20),
        alerts_to=_ALERTS_BY_MODE.get(int(mode or 0), "dept_only"),
        ticket_auto_response=ticket_auto,
        message_auto_response=message_auto,
        members=[DepartmentMember(**m) for m in members],
    )


async def get_department(session: AsyncSession, dept_id: int) -> DepartmentDetail | None:
    row = await repo.get_department(session, dept_id)
    if not row:
        return None
    return _department_detail(dict(row), await repo.get_department_members(session, dept_id))


async def create_department(
    session: AsyncSession,
    body: DepartmentCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="dept.create",
        aggregate_type="dept",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_department(
    session: AsyncSession,
    dept_id: int,
    body: DepartmentUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="dept.update",
        aggregate_type="dept",
        aggregate_id=str(dept_id),
        payload={"dept_id": dept_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_department(
    session: AsyncSession,
    dept_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="dept.delete",
        aggregate_type="dept",
        aggregate_id=str(dept_id),
        payload={"dept_id": dept_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── Teams ─────────────────────────────────────────────────────────────────────

async def list_teams(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> TeamListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_teams(session, limit=page_size, offset=offset)
    
    items = [
        TeamListItem(
            id=row["id"],
            name=row["name"],
            is_enabled=bool(row["is_enabled"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return TeamListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_team(session: AsyncSession, team_id: int) -> TeamDetail | None:
    row = await repo.get_team(session, team_id)
    if not row:
        return None
    members = await repo.get_team_members(session, team_id)
    return TeamDetail(
        **row,
        members=members,
    )

async def create_team(
    session: AsyncSession,
    body: TeamCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="team.create",
        aggregate_type="team",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_team(
    session: AsyncSession,
    team_id: int,
    body: TeamUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="team.update",
        aggregate_type="team",
        aggregate_id=str(team_id),
        payload={"team_id": team_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_team(
    session: AsyncSession,
    team_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="team.delete",
        aggregate_type="team",
        aggregate_id=str(team_id),
        payload={"team_id": team_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── Staff ─────────────────────────────────────────────────────────────────────

async def list_staff(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> StaffListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_staff(session, limit=page_size, offset=offset)
    
    items = [
        StaffListItem(
            id=row["id"],
            username=row["username"],
            firstname=row["firstname"],
            lastname=row["lastname"],
            email=row["email"],
            is_active=bool(row["is_active"]),
            is_admin=bool(row["is_admin"]),
            dept_id=row["dept_id"],
            role_id=row["role_id"],
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return StaffListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_staff(session: AsyncSession, staff_id: int) -> StaffDetail | None:
    row = await repo.get_staff(session, staff_id)
    if not row:
        return None
    teams = await repo.get_staff_teams(session, staff_id)
    row_dict = dict(row)
    held = _json_object(row_dict.pop("permissions", None))
    extra = _json_object(row_dict.pop("extra", None))
    access = await repo.get_staff_dept_access(session, staff_id)
    return StaffDetail(
        **row_dict,
        teams=teams,
        permissions=sorted(k for k, v in held.items() if v and k in GLOBAL_PERMISSIONS),
        assign_use_primary_role=bool(extra.get("def_assn_role")),
        department_access=[DepartmentAccess(**a) for a in access],
    )

async def create_staff(
    session: AsyncSession,
    body: StaffCreate,
    requested_staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="staff.create",
        aggregate_type="staff",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=requested_staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_staff(
    session: AsyncSession,
    staff_id: int,
    body: StaffUpdate,
    requested_staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="staff.update",
        aggregate_type="staff",
        aggregate_id=str(staff_id),
        payload={"staff_id": staff_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=requested_staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_staff(
    session: AsyncSession,
    staff_id: int,
    requested_staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="staff.delete",
        aggregate_type="staff",
        aggregate_id=str(staff_id),
        payload={"staff_id": staff_id},
        requested_by_staff_id=requested_staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
