"""Lookups (Topics, SLAs, Priorities) service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.modules.lookups.topic_visibility import visible_topics
from app.integration.osticket.commands_service import dispatch_command
from app.modules.lookups.models import (
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
