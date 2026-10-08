"""Lookups endpoints.

Permissions — the Admin Panel (isAdmin()): help topics, SLA plans and ticket priorities are managed by
administrators only, and their details and SLAs are admin-only to read. The two *lists* an agent needs to
fill in a ticket form are open to any agent, as in osTicket: ``GET /topics`` (active topics, narrowed by
department) and ``GET /priorities``.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin, require_staff
from app.modules.lookups import service
from app.modules.lookups.models import (
    TicketStatusAdminList,
    TicketStatusCreate,
    TicketStatusDetail,
    TicketStatusListResponse,
    TicketStatusUpdate,
    CommandResponse,
    TopicCreate,
    TopicUpdate,
    TopicDetail,
    TopicListResponse,
    SLACreate,
    SLAUpdate,
    SLADetail,
    SLAListResponse,
    PriorityCreate,
    PriorityUpdate,
    PriorityDetail,
    PriorityListResponse,
)

router = APIRouter(tags=["lookups"])

# ── Help Topics ───────────────────────────────────────────────────────────────

@router.get("/topics", response_model=TopicListResponse, summary="List help topics")
async def list_topics(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """Help topics. Administrators get the Admin Panel list (every topic, raw names). An agent gets what osTicket's
    ticket form offers them: active topics, named "Parent / Child", narrowed by department."""
    return await service.list_topics(
        session, page=page, page_size=page_size, access=current_user["access"]
    )

@router.get("/topics/{topic_id}", response_model=TopicDetail, summary="Get help topic")
async def get_topic(
    topic_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    topic = await service.get_topic(session, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic

@router.post("/topics", response_model=CommandResponse, status_code=202, summary="Create help topic")
async def create_topic(
    body: TopicCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_topic(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/topics/{topic_id}", response_model=CommandResponse, status_code=202, summary="Update help topic")
async def update_topic(
    topic_id: int,
    body: TopicUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_topic(session, topic_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/topics/{topic_id}", response_model=CommandResponse, status_code=202, summary="Delete help topic")
async def delete_topic(
    topic_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_topic(session, topic_id, current_user["staff_id"], idempotency_key)


# ── SLAs ──────────────────────────────────────────────────────────────────────

@router.get("/slas", response_model=SLAListResponse, summary="List SLAs")
async def list_slas(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_slas(session, page=page, page_size=page_size)

@router.get("/slas/{sla_id}", response_model=SLADetail, summary="Get SLA")
async def get_sla(
    sla_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    sla = await service.get_sla(session, sla_id)
    if not sla:
        raise HTTPException(status_code=404, detail="SLA not found")
    return sla

@router.post("/slas", response_model=CommandResponse, status_code=202, summary="Create SLA")
async def create_sla(
    body: SLACreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_sla(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/slas/{sla_id}", response_model=CommandResponse, status_code=202, summary="Update SLA")
async def update_sla(
    sla_id: int,
    body: SLAUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_sla(session, sla_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/slas/{sla_id}", response_model=CommandResponse, status_code=202, summary="Delete SLA")
async def delete_sla(
    sla_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_sla(session, sla_id, current_user["staff_id"], idempotency_key)


# ── Priorities ────────────────────────────────────────────────────────────────

@router.get("/priorities", response_model=PriorityListResponse, summary="List priorities")
async def list_priorities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """Ticket priorities — osTicket offers them to every agent (``Priority::getPriorities``)."""
    return await service.list_priorities(session, page=page, page_size=page_size)

@router.get("/ticket-statuses", response_model=TicketStatusListResponse, summary="List ticket statuses")
async def list_ticket_statuses(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """The enabled ticket statuses, as osTicket offers them to every agent on its ticket forms."""
    return await service.list_ticket_statuses(session)


@router.get("/ticket-statuses/manage", response_model=TicketStatusAdminList, summary="Every ticket status, as the Admin Panel lists them")
async def manage_ticket_statuses(
    current_user: dict = require_admin(),   # Admin Panel > Manage > Lists > Ticket Statuses (admin.inc.php -> isAdmin())
    session: AsyncSession = Depends(get_session),
):
    """Enabled and disabled statuses with their description, reopen settings, ticket counts and whether each is internal
    or deletable. ``GET /ticket-statuses`` is the agents' list of enabled statuses."""
    return await service.list_ticket_status_details(session)


@router.get("/ticket-statuses/{status_id}", response_model=TicketStatusDetail, summary="Get a ticket status")
async def get_ticket_status(
    status_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    found = await service.get_ticket_status(session, status_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Ticket status not found")
    return found


@router.post("/ticket-statuses", response_model=CommandResponse, status_code=202, summary="Create a ticket status")
async def create_ticket_status(
    body: TicketStatusCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_ticket_status(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/ticket-statuses/{status_id}", response_model=CommandResponse, status_code=202, summary="Update a ticket status")
async def update_ticket_status(
    status_id: int,
    body: TicketStatusUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_ticket_status(session, status_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/ticket-statuses/{status_id}", response_model=CommandResponse, status_code=202, summary="Delete a ticket status")
async def delete_ticket_status(
    status_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_ticket_status(session, status_id, current_user["staff_id"], idempotency_key)


@router.get("/priorities/{priority_id}", response_model=PriorityDetail, summary="Get priority")
async def get_priority(
    priority_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    priority = await service.get_priority(session, priority_id)
    if not priority:
        raise HTTPException(status_code=404, detail="Priority not found")
    return priority

@router.post("/priorities", response_model=CommandResponse, status_code=202, summary="Create priority")
async def create_priority(
    body: PriorityCreate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_priority(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/priorities/{priority_id}", response_model=CommandResponse, status_code=202, summary="Update priority")
async def update_priority(
    priority_id: int,
    body: PriorityUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_priority(session, priority_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/priorities/{priority_id}", response_model=CommandResponse, status_code=202, summary="Delete priority")
async def delete_priority(
    priority_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_priority(session, priority_id, current_user["staff_id"], idempotency_key)
