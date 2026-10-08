"""Lookups (Topics, SLAs, Priorities) service layer."""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.modules.lookups.topic_visibility import visible_topics
from app.integration.osticket.commands_service import dispatch_command
from app.modules.lookups.models import (
    TicketStatusAdminList,
    TicketStatusCreate,
    TicketStatusDetail,
    TicketStatusItem,
    TicketStatusListResponse,
    TicketStatusUpdate,
    CommandResponse,
    TopicCreate,
    TopicUpdate,
    TopicListItem,
    TopicDetail,
    TopicListResponse,
    SLACreate,
    SLAUpdate,
    SLAListItem,
    SLADetail,
    SLAListResponse,
    PriorityCreate,
    PriorityUpdate,
    PriorityListItem,
    PriorityDetail,
    PriorityListResponse,
)

logger = logging.getLogger(__name__)


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Help Topics ───────────────────────────────────────────────────────────────

async def list_topics(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
    *,
    access,
) -> TopicListResponse:
    """Help topics. An administrator gets the Admin Panel's list (every topic, active or not, raw names);
    an agent gets what osTicket's ticket form offers them (``Staff::getTopicNames``): active topics with their
    full "Parent / Child" names, narrowed by department unless they hold ``visibility.departments``."""
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    if not access.is_admin:
        rows = await repo.list_topic_rows(session)
        by_id = {int(r["topic_id"]): r for r in rows}
        visible = visible_topics(
            rows,
            staff_dept_ids=access.dept_ids,
            filter_by_department=bool(access.dept_ids) and not access.has_global("visibility.departments"),
        )
        # help_topic_sort_mode 'a' (osTicket's default) lists topics alphabetically by full name; 'm' keeps the
        # manual order the rows already come in
        if await repo.get_help_topic_sort_mode(session) != "m":
            visible.sort(key=lambda entry: entry[1].casefold())
        return TopicListResponse(
            items=[
                TopicListItem(
                    id=tid,
                    topic=name,
                    is_active=True,
                    is_public=bool(by_id[tid]["ispublic"]),
                    created=by_id[tid]["created"],
                    updated=by_id[tid]["updated"],
                )
                for tid, name in visible[offset : offset + page_size]
            ],
            total=len(visible),
            page=page,
            page_size=page_size,
        )
    rows = await repo.list_topics(session, limit=page_size, offset=offset)
    items = [
        TopicListItem(
            id=row["id"],
            topic=row["topic"],
            is_active=bool(row["is_active"]),
            is_public=bool(row["is_public"]),
            created=row["created"],
            updated=row["updated"],
            path=row.get("path"),
            priority_name=row.get("priority_name"),
            dept_name=row.get("dept_name"),
        )
        for row in rows
    ]
    
    return TopicListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_topic(session: AsyncSession, topic_id: int) -> TopicDetail | None:
    row = await repo.get_topic(session, topic_id)
    if not row:
        return None
    row = dict(row)
    flags = int(row.pop("flags", 0) or 0)   # Topic::FLAG_CUSTOM_NUMBERS 1, FLAG_ACTIVE 2, FLAG_ARCHIVED 4
    return TopicDetail(
        **row,
        status="archived" if flags & 0x4 else ("active" if flags & 0x2 else "disabled"),
        custom_numbers=bool(flags & 0x1),
        form_ids=await repo.get_topic_form_ids(session, topic_id),
    )


async def create_topic(
    session: AsyncSession,
    body: TopicCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="topic.create",
        aggregate_type="topic",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_topic(
    session: AsyncSession,
    topic_id: int,
    body: TopicUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="topic.update",
        aggregate_type="topic",
        aggregate_id=str(topic_id),
        payload={"topic_id": topic_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_topic(
    session: AsyncSession,
    topic_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="topic.delete",
        aggregate_type="topic",
        aggregate_id=str(topic_id),
        payload={"topic_id": topic_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── SLAs ──────────────────────────────────────────────────────────────────────

async def list_slas(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> SLAListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_slas(session, limit=page_size, offset=offset)
    
    items = [
        SLAListItem(
            id=row["id"],
            name=row["name"],
            grace_period=row["grace_period"],
            is_active=bool(row["is_active"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return SLAListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_sla(session: AsyncSession, sla_id: int) -> SLADetail | None:
    row = await repo.get_sla(session, sla_id)
    if not row:
        return None
    return SLADetail(**row)


async def create_sla(
    session: AsyncSession,
    body: SLACreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="sla.create",
        aggregate_type="sla",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_sla(
    session: AsyncSession,
    sla_id: int,
    body: SLAUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="sla.update",
        aggregate_type="sla",
        aggregate_id=str(sla_id),
        payload={"sla_id": sla_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_sla(
    session: AsyncSession,
    sla_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="sla.delete",
        aggregate_type="sla",
        aggregate_id=str(sla_id),
        payload={"sla_id": sla_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── Priorities ────────────────────────────────────────────────────────────────

async def list_priorities(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> PriorityListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_priorities(session, limit=page_size, offset=offset)
    
    items = [
        PriorityListItem(
            id=row["id"],
            name=row["name"],
            desc=row["desc"],
            urgency=row["urgency"],
            is_public=bool(row["is_public"]),
        )
        for row in rows
    ]
    
    return PriorityListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def list_ticket_statuses(session: AsyncSession) -> TicketStatusListResponse:
    rows = await repo.list_ticket_statuses(session, limit=200, offset=0)
    return TicketStatusListResponse(statuses=[TicketStatusItem(id=r["id"], name=r["name"], state=r["state"]) for r in rows])


def _status_detail(row, default_id: int) -> TicketStatusDetail:
    try:
        props = json.loads(row["properties"]) if row["properties"] else {}
    except ValueError:
        props = {}
    if not isinstance(props, dict):
        props = {}
    mode, tickets = int(row["mode"] or 0), int(row["ticket_count"] or 0)
    internal = bool(mode & 2) or int(row["id"]) == default_id
    reopen = props.get("reopenstatus")
    return TicketStatusDetail(
        id=row["id"], name=row["name"], state=row["state"],
        description=str(props.get(str(row.get("description_field_id"))) if str(row.get("description_field_id")) in props
                        else props.get("description") or ""),
        sort=int(row["sort"] or 0), is_enabled=bool(mode & 1), is_internal=internal, is_default=int(row["id"]) == default_id,
        allow_reopen=bool(props.get("allowreopen")), reopen_status_id=int(reopen) if str(reopen or "").isdigit() and int(reopen) else None,
        ticket_count=tickets, is_deletable=not internal and tickets == 0)


async def _default_status_id(session: AsyncSession) -> int:
    stored = (await repo.get_core_config(session)).get("default_ticket_status_id")
    return int(stored) if str(stored or "").isdigit() else 0


async def list_ticket_status_details(session: AsyncSession) -> TicketStatusAdminList:
    default_id = await _default_status_id(session)
    rows = await repo.list_ticket_status_details(session, limit=500, offset=0)
    return TicketStatusAdminList(items=[_status_detail(r, default_id) for r in rows])


async def get_ticket_status(session: AsyncSession, status_id: int) -> TicketStatusDetail | None:
    row = await repo.get_ticket_status_detail(session, status_id)
    return _status_detail(row, await _default_status_id(session)) if row else None


async def create_ticket_status(session: AsyncSession, body: TicketStatusCreate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="ticket_status.create", aggregate_type="ticket_status", aggregate_id="",
        payload=body.model_dump(exclude_none=True), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def update_ticket_status(session: AsyncSession, status_id: int, body: TicketStatusUpdate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="ticket_status.update", aggregate_type="ticket_status", aggregate_id=str(status_id),
        payload={"status_id": status_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key)
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def delete_ticket_status(session: AsyncSession, status_id: int, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="ticket_status.delete", aggregate_type="ticket_status", aggregate_id=str(status_id),
        payload={"status_id": status_id}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def get_priority(session: AsyncSession, priority_id: int) -> PriorityDetail | None:
    row = await repo.get_priority(session, priority_id)
    if not row:
        return None
    return PriorityDetail(**row)


async def create_priority(
    session: AsyncSession,
    body: PriorityCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="priority.create",
        aggregate_type="priority",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_priority(
    session: AsyncSession,
    priority_id: int,
    body: PriorityUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="priority.update",
        aggregate_type="priority",
        aggregate_id=str(priority_id),
        payload={"priority_id": priority_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_priority(
    session: AsyncSession,
    priority_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="priority.delete",
        aggregate_type="priority",
        aggregate_id=str(priority_id),
        payload={"priority_id": priority_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
