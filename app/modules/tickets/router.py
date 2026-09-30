"""Ticket endpoints.

All writes return CommandResponse — the command is queued for async PHP execution.
All reads query osTicket's tables directly via read_repository.

Permission map (role permissions — any of the agent's roles; the bridge applies osTicket's exact
per-department check when it runs the command). Reads still use ``ticket.create`` as a stand-in until
read scoping (S3) replaces it with osTicket's department/assignment visibility:
  ticket.create     ticket.reply      ticket.note (→ ticket.reply used for notes)
  ticket.close      ticket.assign     ticket.transfer
  ticket.delete     ticket.merge      ticket.markanswered
  ticket.edit       (change owner uses ticket.edit)
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.tickets import service
from app.modules.tickets.models import (
    CommandResponse,
    TicketAssign,
    TicketAssignTeam,
    TicketChangeOwner,
    TicketClose,
    TicketCollaboratorAdd,
    TicketCreate,
    TicketDetail,
    TicketListResponse,
    TicketMerge,
    TicketNote,
    TicketReopen,
    TicketReply,
    TicketTransfer,
)

router = APIRouter(tags=["tickets"])

# ── Reads ──────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=TicketListResponse,
    summary="List tickets",
)
async def list_tickets(
    status_id: int | None = Query(default=None, description="Filter by status ID"),
    dept_id: int | None = Query(default=None, description="Filter by department ID"),
    assignee_staff_id: int | None = Query(default=None, description="Filter by assigned staff"),
    assignee_team_id: int | None = Query(default=None, description="Filter by assigned team"),
    topic_id: int | None = Query(default=None, description="Filter by help topic ID"),
    sla_id: int | None = Query(default=None, description="Filter by SLA plan ID"),
    is_answered: bool | None = Query(default=None, description="Filter by answered flag"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=50, ge=1, le=200, description="Rows per page"),
    current_user: dict = require_perm("ticket.create"),
    session: AsyncSession = Depends(get_session),
):
    """Return a paginated, filtered list of tickets.

    Requires any of the ticket permissions (we gate on ``ticket.create`` as the
    baseline — any staff member who can work on tickets has this).
    """
    return await service.list_tickets(
        session,
        status_id=status_id,
        dept_id=dept_id,
        assignee_staff_id=assignee_staff_id,
        assignee_team_id=assignee_team_id,
        topic_id=topic_id,
        sla_id=sla_id,
        is_answered=is_answered,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{ticket_id}",
    response_model=TicketDetail,
    summary="Get a single ticket with its thread and collaborators",
)
async def get_ticket(
    ticket_id: int,
    current_user: dict = require_perm("ticket.create"),
    session: AsyncSession = Depends(get_session),
):
    ticket = await service.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return ticket


# ── Writes ─────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=CommandResponse,
    status_code=202,
    summary="Create a ticket",
)
async def create_ticket(
    body: TicketCreate,
    current_user: dict = require_perm("ticket.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_ticket(
        session, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/reply",
    response_model=CommandResponse,
    status_code=202,
    summary="Post a reply to a ticket",
)
async def reply_to_ticket(
    ticket_id: int,
    body: TicketReply,
    current_user: dict = require_perm("ticket.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.reply_to_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/note",
    response_model=CommandResponse,
    status_code=202,
    summary="Add an internal note to a ticket",
)
async def add_note(
    ticket_id: int,
    body: TicketNote,
    current_user: dict = require_perm("ticket.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_note(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/close",
    response_model=CommandResponse,
    status_code=202,
    summary="Close a ticket",
)
async def close_ticket(
    ticket_id: int,
    body: TicketClose = TicketClose(),
    current_user: dict = require_perm("ticket.close"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.close_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/reopen",
    response_model=CommandResponse,
    status_code=202,
    summary="Re-open a closed ticket",
)
async def reopen_ticket(
    ticket_id: int,
    body: TicketReopen = TicketReopen(),
    current_user: dict = require_perm("ticket.close"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.reopen_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/assign",
    response_model=CommandResponse,
    status_code=202,
    summary="Assign a ticket to a staff member",
)
async def assign_ticket(
    ticket_id: int,
    body: TicketAssign,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.assign_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/assign-team",
    response_model=CommandResponse,
    status_code=202,
    summary="Assign a ticket to a team",
)
async def assign_team(
    ticket_id: int,
    body: TicketAssignTeam,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.assign_team(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/transfer",
    response_model=CommandResponse,
    status_code=202,
    summary="Transfer a ticket to another department",
)
async def transfer_ticket(
    ticket_id: int,
    body: TicketTransfer,
    current_user: dict = require_perm("ticket.transfer"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.transfer_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/change-owner",
    response_model=CommandResponse,
    status_code=202,
    summary="Change the ticket's end-user owner",
)
async def change_owner(
    ticket_id: int,
    body: TicketChangeOwner,
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.change_owner(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.delete(
    "/{ticket_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Delete a ticket",
)
async def delete_ticket(
    ticket_id: int,
    comments: str = Query(default="", description="Optional deletion comments"),
    current_user: dict = require_perm("ticket.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_ticket(
        session, ticket_id, current_user["staff_id"], idempotency_key, comments
    )


@router.post(
    "/{ticket_id}/merge",
    response_model=CommandResponse,
    status_code=202,
    summary="Merge one or more tickets into this ticket",
)
async def merge_tickets(
    ticket_id: int,
    body: TicketMerge,
    current_user: dict = require_perm("ticket.merge"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.merge_tickets(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/answered",
    response_model=CommandResponse,
    status_code=202,
    summary="Mark a ticket as answered",
)
async def mark_answered(
    ticket_id: int,
    current_user: dict = require_perm("ticket.markanswered"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.set_answered(
        session, ticket_id, True, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/unanswered",
    response_model=CommandResponse,
    status_code=202,
    summary="Mark a ticket as unanswered",
)
async def mark_unanswered(
    ticket_id: int,
    current_user: dict = require_perm("ticket.markanswered"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.set_answered(
        session, ticket_id, False, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/collaborators",
    response_model=CommandResponse,
    status_code=202,
    summary="Add a collaborator to a ticket",
)
async def add_collaborator(
    ticket_id: int,
    body: TicketCollaboratorAdd,
    current_user: dict = require_perm("ticket.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_collaborator(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )
