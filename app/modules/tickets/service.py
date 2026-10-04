"""Ticket service layer.

Reads: thin wrappers over read_repository that shape raw rows into response models.
Writes: dispatch commands via the integration inbox.

All write functions return a CommandResponse.  Callers pass the idempotency key
extracted from the request header (may be None if the client didn't supply one).
"""

import logging
from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_fields import BUILT_IN_FIELDS, CustomFieldAnswer
from app.core.attachments import group_by_entry
from app.integration.osticket import read_repository as repo
from app.modules.agents import service as agents_service
from app.integration.osticket.commands_service import dispatch_command
from app.modules.tickets.models import (
    Attachment,
    QueueCount,
    QueueCounts,
    Collaborator,
    CommandResponse,
    ThreadEntry,
    CollaboratorList,
    TicketCollaboratorAdd,
    TicketEntryEdit,
    TicketEntryResend,
    TicketCollaboratorUpdate,
    TicketCreate,
    TicketDetail,
    TicketListItem,
    TicketListResponse,
    TicketNote,
    TicketReply,
    TicketStatusSchema,
    TicketAssign,
    TicketAssignTeam,
    TicketChangeOwner,
    TicketClose,
    TicketMerge,
    TicketReopen,
    TicketClaim,
    TicketRefer,
    TicketRelease,
    TicketTransfer,
    TicketUpdate,
)

logger = logging.getLogger(__name__)


# ── Row → model helpers ────────────────────────────────────────────────────────


def _row_to_list_item(row: dict) -> TicketListItem:
    return TicketListItem(
        ticket_id=row["ticket_id"],
        number=row["number"],
        subject=row.get("subject") or "",
        status=TicketStatusSchema(
            id=row["status_id"],
            name=row.get("status_name") or "",
            state=row.get("status_state") or "open",
        ),
        dept_id=row.get("dept_id"),
        dept_name=row.get("dept_name"),
        assignee_staff_id=row.get("assignee_staff_id"),
        assignee_name=row.get("assignee_name"),
        team_id=row.get("team_id"),
        team_name=row.get("team_name"),
        user_id=row.get("user_id"),
        user_name=row.get("user_name"),
        topic_id=row.get("topic_id"),
        topic_name=row.get("topic_name"),
        sla_id=row.get("sla_id"),
        sla_name=row.get("sla_name"),
        priority_id=row.get("priority_id"),
        priority_name=row.get("priority_name"),
        source=row.get("source") or "",
        is_answered=bool(row.get("is_answered", 0)),
        created=row["created"],
        updated=row["updated"],
        closed=row.get("closed"),
        due_date=row.get("due_date"),
    )


def _row_to_thread_entry(row: dict, attachments: list[dict] | None = None) -> ThreadEntry:
    return ThreadEntry(
        attachments=[Attachment(**{k: a[k] for k in ('id', 'name', 'size', 'content_type', 'inline')}) for a in attachments or []],
        id=row["id"],
        type=row.get("type") or "M",
        title=row.get("title"),
        body=row.get("body") or "",
        staff_id=row.get("staff_id") or None,
        user_id=row.get("user_id") or None,
        poster=row.get("poster"),
        ip_address=row.get("ip_address"),
        created=row["created"],
        is_edited=bool(row.get("is_edited", False)),
    )


def _custom_answers(rows, object_type: str) -> list[CustomFieldAnswer]:
    skip = BUILT_IN_FIELDS[object_type]
    return [
        CustomFieldAnswer(name=r["name"], label=r["label"], type=r["type"], value=r["value"], value_id=r["value_id"])
        for r in map(dict, rows)
        if r["name"] not in skip
    ]


def _row_to_collaborator(row: dict) -> Collaborator:
    return Collaborator(
        id=row["id"],
        user_id=row["user_id"],
        name=row.get("name") or "",
        email=row.get("email") or "",
        is_active=bool(row.get("is_active", True)),
        is_cc=bool(row.get("is_cc", True)),
    )


def _make_cmd_response(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Reads ──────────────────────────────────────────────────────────────────────


async def list_tickets(
    session: AsyncSession,
    *,
    access,
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    assignee_team_id: int | None = None,
    topic_id: int | None = None,
    sla_id: int | None = None,
    is_answered: bool | None = None,
    q: str | None = None,
    queue: str | None = None,
    sort: str = "updated",
    descending: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> TicketListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    filters = dict(
        status_id=status_id, dept_id=dept_id, assignee_staff_id=assignee_staff_id,
        assignee_team_id=assignee_team_id, topic_id=topic_id, sla_id=sla_id, is_answered=is_answered, q=q, queue=queue,
    )
    rows = await repo.list_tickets(session, access=access, **filters, sort=sort, descending=descending, limit=page_size, offset=offset)
    total = await repo.count_tickets(session, access=access, **filters)
    return TicketListResponse(
        items=[_row_to_list_item(dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


_QUEUE_TITLES = (   # osTicket's default queues, parents first with their children after (ost_queue ids 1-8)
    ("open", "Open", None), ("unanswered", "Open", "open"), ("answered", "Answered", "open"),
    ("overdue", "Overdue", "open"), ("mine", "My Tickets", None), ("assigned_me", "Assigned to Me", "mine"),
    ("assigned_teams", "Assigned to Teams", "mine"), ("closed", "Closed", None),
)


async def queue_counts(session: AsyncSession, *, access) -> QueueCounts:
    counts = await repo.count_ticket_queues(session, access=access)
    return QueueCounts(queues=[QueueCount(key=key, title=title, parent=parent, count=counts[key])
                               for key, title, parent in _QUEUE_TITLES])


async def get_ticket(session: AsyncSession, ticket_id: int, *, access) -> TicketDetail | None:
    """The ticket with its thread, or None if it does not exist or ``access`` may not open it."""
    row = await repo.get_ticket(session, ticket_id, access=access)
    if row is None:
        return None
    thread_rows = await repo.get_ticket_thread(session, ticket_id, access=access)
    files = group_by_entry(await repo.get_ticket_attachments(session, ticket_id, access=access))
    collab_rows = await repo.get_ticket_collaborators(session, ticket_id, access=access)
    custom_rows = await repo.get_ticket_custom_fields(session, ticket_id, access=access)
    item = _row_to_list_item(row)
    return TicketDetail(
        **item.model_dump(),
        thread=[_row_to_thread_entry(dict(r), files.get(int(r['id']))) for r in thread_rows],
        collaborators=[_row_to_collaborator(dict(r)) for r in collab_rows],
        custom_fields=_custom_answers(custom_rows, "T"),
    )


async def ticket_assignees(session: AsyncSession, ticket_id: int, *, access):
    """The agents and teams the caller may assign the ticket to (osTicket's assign form), or None if the ticket
    does not exist or may not be opened. Raises PermissionError when the caller's role in the ticket's department
    lacks ``ticket.assign`` (``Ticket::checkStaffPerm(PERM_ASSIGN)``)."""
    row = await repo.get_ticket(session, ticket_id, access=access)
    if row is None:
        return None
    if not access.can_role_perm_in("ticket.assign", int(row["dept_id"] or 0)):
        raise PermissionError("ticket.assign")
    return await agents_service.assignee_options(session, access=access, dept_id=int(row["dept_id"] or 0))


async def list_collaborators(session: AsyncSession, ticket_id: int, *, access) -> CollaboratorList | None:
    """The ticket's collaborators, or None if the ticket does not exist or ``access`` may not open it."""
    if await repo.get_ticket(session, ticket_id, access=access) is None:
        return None
    rows = await repo.get_ticket_collaborators(session, ticket_id, access=access)
    return CollaboratorList(collaborators=[_row_to_collaborator(dict(r)) for r in rows])


# ── Writes ─────────────────────────────────────────────────────────────────────


async def create_ticket(
    session: AsyncSession,
    body: TicketCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.create",
        aggregate_type="ticket",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def reply_to_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketReply,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.reply",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def add_note(
    session: AsyncSession,
    ticket_id: int,
    body: TicketNote,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.note",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def close_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketClose,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.close",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def reopen_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketReopen,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.reopen",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def assign_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketAssign,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.assign",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def assign_team(
    session: AsyncSession,
    ticket_id: int,
    body: TicketAssignTeam,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.assign_team",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def transfer_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketTransfer,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.transfer",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def update_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    fields = body.model_dump(exclude_unset=True)
    if "due_date" in fields and fields["due_date"] is not None:
        due = fields["due_date"]  # naive = UTC
        if due.tzinfo is not None:
            due = due.astimezone(timezone.utc)
        fields["due_date"] = due.strftime("%Y-%m-%d %H:%M:%S")
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.update",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **fields},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def claim_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketClaim,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.claim",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def release_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketRelease,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.release",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def refer_ticket(
    session: AsyncSession,
    ticket_id: int,
    body: TicketRefer,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.refer",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def change_owner(
    session: AsyncSession,
    ticket_id: int,
    body: TicketChangeOwner,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.change_owner",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def delete_ticket(
    session: AsyncSession,
    ticket_id: int,
    staff_id: int,
    idempotency_key: str | None,
    comments: str = "",
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.delete",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, "comments": comments},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def merge_tickets(
    session: AsyncSession,
    ticket_id: int,
    body: TicketMerge,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.merge",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def set_answered(
    session: AsyncSession,
    ticket_id: int,
    answered: bool,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd_type = "ticket.answered" if answered else "ticket.unanswered"
    cmd = await dispatch_command(
        session=session,
        command_type=cmd_type,
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def add_collaborator(
    session: AsyncSession,
    ticket_id: int,
    body: TicketCollaboratorAdd,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.collaborator.add",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def update_collaborator(
    session: AsyncSession,
    ticket_id: int,
    user_id: int,
    body: TicketCollaboratorUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.collaborator.update",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, "user_id": user_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def remove_collaborator(
    session: AsyncSession,
    ticket_id: int,
    user_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.collaborator.remove",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, "user_id": user_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def edit_entry(
    session: AsyncSession,
    ticket_id: int,
    entry_id: int,
    body: TicketEntryEdit,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.entry.edit",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, "entry_id": entry_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def resend_entry(
    session: AsyncSession,
    ticket_id: int,
    entry_id: int,
    body: TicketEntryResend,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="ticket.entry.resend",
        aggregate_type="ticket",
        aggregate_id=str(ticket_id),
        payload={"ticket_id": ticket_id, "entry_id": entry_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _make_cmd_response(cmd, idempotency_key)


async def ticket_attachment(session: AsyncSession, ticket_id: int, attachment_id: int, *, access) -> dict | None:
    """The attachment's file row if it is attached to this ticket's thread and the ticket may be opened, else None."""
    rows = await repo.get_ticket_attachments(session, ticket_id, access=access, attachment_id=attachment_id)
    return dict(rows[0]) if rows else None
