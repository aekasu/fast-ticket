"""Ticket endpoints.

All writes return CommandResponse — the command is queued for async PHP execution.
All reads query osTicket's tables directly via read_repository.

Reads need no permission: what an agent sees is decided by osTicket's visibility rules, applied in the
query (``read_repository.ticket_visibility``); a ticket they may not open is a 404.

Writes — permission map (role permissions — any of the agent's roles; the bridge applies osTicket's exact
per-department check when it runs the command):
  ticket.create     ticket.reply      ticket.note (→ ticket.reply used for notes)
  ticket.close      ticket.assign (assign, claim, refer, remove referral)     ticket.transfer
  ticket.release    (release / unassign)
  ticket.delete     ticket.merge      ticket.markanswered
  ticket.edit       (edit fields, change owner)
  ticket.reply      also gates thread-entry edit / resend (the bridge applies the exact rule: own post, manager, thread.edit)
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachments import file_response
from app.core.custom_fields import custom_fields_query
from app.core.db import get_session
from app.core.security import require_perm, require_staff
from app.modules.agents.models import AssigneeOptions
from app.modules.tickets import service
from app.modules.tickets.models import (
    QueueCounts,
    TicketQueue,
    TicketSort,
    CommandResponse,
    TicketAssign,
    TicketAssignTeam,
    TicketChangeOwner,
    TicketClose,
    CollaboratorList,
    CannedRendered,
    ReferralList,
    TicketLink,
    TicketRelations,
    TicketCollaboratorAdd,
    TicketEntryEdit,
    TicketEntryResend,
    TicketCollaboratorUpdate,
    TicketCreate,
    TicketDetail,
    TicketListResponse,
    TicketMerge,
    TicketNote,
    TicketReopen,
    TicketReply,
    TicketClaim,
    TicketRefer,
    TicketRelease,
    TicketTransfer,
    TicketUpdate,
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
    user_id: int | None = Query(default=None, description="Filter to the tickets of one end user (the requester)"),
    is_answered: bool | None = Query(default=None, description="Filter by answered flag"),
    q: str | None = Query(default=None, max_length=100, description="Ticket number, subject, or the requester's name or email (contains)"),
    queue: TicketQueue | None = Query(default=None, description="One of osTicket's default queues (see GET /tickets/queues)"),
    sort: TicketSort = Query(default="updated"),
    descending: bool = Query(default=True),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=50, ge=1, le=200, description="Rows per page"),
    current_user: dict = require_staff(),
    field_names: list[str] | None = Depends(custom_fields_query),
    session: AsyncSession = Depends(get_session),
):
    """Return a paginated, filtered list of the tickets the agent may see.

    Visibility is osTicket's own (``Staff::getTicketsVisibility``): tickets that are open and assigned to
    the agent or their team (or referred to either), and tickets of their departments — unless the agent
    is limited to assigned-only. No permission is needed to view, as in osTicket.
    """
    return await service.list_tickets(
        session,
        access=current_user["access"],
        status_id=status_id,
        dept_id=dept_id,
        assignee_staff_id=assignee_staff_id,
        assignee_team_id=assignee_team_id,
        topic_id=topic_id,
        sla_id=sla_id,
        user_id=user_id,
        is_answered=is_answered,
        q=q,
        queue=queue,
        sort=sort,
        descending=descending,
        page=page,
        page_size=page_size,
        field_names=field_names,
    )


@router.get(
    "/queues",
    response_model=QueueCounts,
    summary="Ticket counts for osTicket's default queues",
)
async def ticket_queues(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """Open (unanswered / answered / overdue), My Tickets (assigned to me / to my teams) and Closed — each counted
    among the tickets the agent's own list shows."""
    return await service.queue_counts(session, access=current_user["access"])


@router.get(
    "/{ticket_id}",
    response_model=TicketDetail,
    summary="Get a single ticket with its thread and collaborators",
)
async def get_ticket(
    ticket_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    # 404, not 403, for a ticket the agent may not open: its existence is not disclosed.
    ticket = await service.get_ticket(session, ticket_id, access=current_user["access"])
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return ticket


@router.get(
    "/{ticket_id}/attachments/{attachment_id}",
    summary="Download a file attached to one of the ticket's thread entries",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    # 404 for a ticket the agent may not open, and for a file that is not attached to it.
    row = await service.ticket_attachment(session, ticket_id, attachment_id, access=current_user["access"])
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return await file_response(row, staff_id=current_user["staff_id"], object_type="T", object_id=ticket_id)


@router.get(
    "/{ticket_id}/assignees",
    response_model=AssigneeOptions,
    summary="Agents and teams this ticket can be assigned to",
)
async def ticket_assignees(
    ticket_id: int,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await service.ticket_assignees(session, ticket_id, access=current_user["access"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied: ticket.assign is required in the ticket's department.")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return result


@router.get(
    "/{ticket_id}/collaborators",
    response_model=CollaboratorList,
    summary="List a ticket's collaborators",
)
async def list_collaborators(
    ticket_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    result = await service.list_collaborators(session, ticket_id, access=current_user["access"])
    if result is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return result


@router.get(
    "/{ticket_id}/canned/{canned_id}",
    response_model=CannedRendered,
    summary="A canned response with this ticket's variables filled in",
)
async def render_canned(
    ticket_id: int,
    canned_id: int,
    plain: bool = Query(default=False, description="Strip the HTML (osTicket's plain-text reply box)"),
    current_user: dict = require_staff(),   # ajax.tickets.php cannedResponse(): the agent must be able to open the ticket
    session: AsyncSession = Depends(get_session),
):
    """osTicket's own variable expansion, asked of osTicket (the bridge's read mode): the picked canned response as the
    reply box should receive it. 404 for a ticket the agent may not open or a response they may not use."""
    result = await service.render_canned(
        session, ticket_id, canned_id, plain, access=current_user["access"], staff_id=current_user["staff_id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Ticket or canned response not found.")
    return result


@router.get(
    "/{ticket_id}/referrals",
    response_model=ReferralList,
    summary="List the agents, teams and departments the ticket is referred to",
)
async def list_referrals(
    ticket_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    result = await service.list_referrals(session, ticket_id, access=current_user["access"])
    if result is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return result


@router.get(
    "/{ticket_id}/related",
    response_model=TicketRelations,
    summary="The tickets linked to or merged with this one",
)
async def get_related(
    ticket_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    result = await service.get_relations(session, ticket_id, access=current_user["access"])
    if result is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")
    return result


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


@router.patch(
    "/{ticket_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Edit a ticket (subject, priority, topic, SLA, source, due date)",
)
async def update_ticket(
    ticket_id: int,
    body: TicketUpdate,
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/claim",
    response_model=CommandResponse,
    status_code=202,
    summary="Assign an open, unassigned ticket to yourself",
)
async def claim_ticket(
    ticket_id: int,
    body: TicketClaim,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.claim_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/release",
    response_model=CommandResponse,
    status_code=202,
    summary="Release (unassign) the agent and/or team assignment",
)
async def release_ticket(
    ticket_id: int,
    body: TicketRelease,
    current_user: dict = require_perm("ticket.release"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.release_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/refer",
    response_model=CommandResponse,
    status_code=202,
    summary="Refer the ticket to an agent, team or department",
)
async def refer_ticket(
    ticket_id: int,
    body: TicketRefer,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.refer_ticket(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.delete(
    "/{ticket_id}/referrals/{referral_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Remove a referral",
)
async def remove_referral(
    ticket_id: int,
    referral_id: int,
    current_user: dict = require_perm("ticket.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.remove_referral(
        session, ticket_id, referral_id, current_user["staff_id"], idempotency_key
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
    "/{ticket_id}/link",
    response_model=CommandResponse,
    status_code=202,
    summary="Link tickets to this one (threads stay apart, nothing is closed)",
)
async def link_tickets(
    ticket_id: int,
    body: TicketLink,
    current_user: dict = require_perm("ticket.link"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.link_tickets(session, ticket_id, body, current_user["staff_id"], idempotency_key)


@router.post(
    "/{ticket_id}/unlink",
    response_model=CommandResponse,
    status_code=202,
    summary="Remove a link (from a parent: all children; from a child: itself)",
)
async def unlink_ticket(
    ticket_id: int,
    current_user: dict = require_perm("ticket.link"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.unlink_ticket(session, ticket_id, current_user["staff_id"], idempotency_key)


@router.post(
    "/{ticket_id}/unmerge",
    response_model=CommandResponse,
    status_code=202,
    summary="Undo a merge (from a parent: all children; from a child: itself)",
)
async def unmerge_ticket(
    ticket_id: int,
    current_user: dict = require_perm("ticket.merge"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.unmerge_ticket(session, ticket_id, current_user["staff_id"], idempotency_key)


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
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_collaborator(
        session, ticket_id, body, current_user["staff_id"], idempotency_key
    )


@router.patch(
    "/{ticket_id}/collaborators/{user_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Change a collaborator's active / CC flags",
)
async def update_collaborator(
    ticket_id: int,
    user_id: int,
    body: TicketCollaboratorUpdate,
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_collaborator(
        session, ticket_id, user_id, body, current_user["staff_id"], idempotency_key
    )


@router.delete(
    "/{ticket_id}/collaborators/{user_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Remove a collaborator from a ticket",
)
async def remove_collaborator(
    ticket_id: int,
    user_id: int,
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.remove_collaborator(
        session, ticket_id, user_id, current_user["staff_id"], idempotency_key
    )


@router.patch(
    "/{ticket_id}/thread/{entry_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Edit a thread entry (the entry gets a new id; the old one is hidden)",
)
async def edit_entry(
    ticket_id: int,
    entry_id: int,
    body: TicketEntryEdit,
    current_user: dict = require_perm("ticket.reply"),   # gate; the bridge applies osTicket's exact edit rule
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.edit_entry(
        session, ticket_id, entry_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{ticket_id}/thread/{entry_id}/resend",
    response_model=CommandResponse,
    status_code=202,
    summary="Resend a staff reply to the ticket's recipients",
)
async def resend_entry(
    ticket_id: int,
    entry_id: int,
    body: TicketEntryResend,
    current_user: dict = require_perm("ticket.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.resend_entry(
        session, ticket_id, entry_id, body, current_user["staff_id"], idempotency_key
    )
