"""Task service layer."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_fields import BUILT_IN_FIELDS, CustomFieldAnswer
from app.core.attachments import group_by_entry
from app.integration.osticket import read_repository as repo
from app.modules.agents import service as agents_service
from app.integration.osticket.commands_service import dispatch_command
from app.modules.tasks.models import (
    TaskAssign,
    TaskClaim,
    TaskClose,
    TaskCreate,
    TaskDetail,
    TaskEdit,
    TaskListItem,
    TaskListResponse,
    TaskQueueCount,
    TaskQueueCounts,
    TaskNote,
    TaskReopen,
    TaskReply,
    TaskTransfer,
    TaskStatusSchema,
)
from app.modules.tickets.models import (
    Attachment,
    Collaborator,
    CollaboratorList,
    CommandResponse,
    ThreadEntry,
    TicketCollaboratorAdd,
    TicketEntryEdit,
    TicketEntryResend,
)

logger = logging.getLogger(__name__)


# ── Row → model helpers ────────────────────────────────────────────────────────


def _row_to_list_item(row: dict) -> TaskListItem:
    return TaskListItem(
        task_id=row["task_id"],
        number=row.get("number") or "",
        title=row.get("title") or "",
        status=TaskStatusSchema(
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
        ticket_id=row.get("ticket_id"),
        ticket_number=row.get("ticket_number"),
        is_overdue=bool(row.get("is_overdue", 0)) and (row.get("status_state") or "open") == "open",
        created=row["created"],
        updated=row["updated"],
        closed=row.get("closed"),
        due_date=row.get("due_date"),
    )


def _row_to_thread_entry(row: dict, attachments: list[dict] | None = None) -> ThreadEntry:
    return ThreadEntry(
        attachments=[Attachment(**{k: a[k] for k in ('id', 'name', 'size', 'content_type', 'inline')}) for a in attachments or []],
        id=row["id"],
        type=row.get("type") or "N",
        title=row.get("title"),
        body=row.get("body") or "",
        staff_id=row.get("staff_id") or None,
        user_id=row.get("user_id") or None,
        poster=row.get("poster"),
        ip_address=row.get("ip_address"),
        created=row["created"],
        format="html" if (row.get("format") or "text") == "html" else "text",
        is_edited=bool(row.get("is_edited", False)),
    )


def _row_to_collaborator(row: dict) -> Collaborator:
    return Collaborator(
        id=row["id"],
        user_id=row["user_id"],
        name=row.get("name") or "",
        email=row.get("email") or "",
        is_active=bool(row.get("is_active", True)),
        is_cc=bool(row.get("is_cc", True)),
    )


def _custom_answers(rows, object_type: str) -> list[CustomFieldAnswer]:
    skip = BUILT_IN_FIELDS[object_type]
    return [
        CustomFieldAnswer(name=r["name"], label=r["label"], type=r["type"], value=r["value"], value_id=r["value_id"])
        for r in map(dict, rows)
        if r["name"] not in skip
    ]


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── Reads ──────────────────────────────────────────────────────────────────────


async def list_tasks(
    session: AsyncSession,
    *,
    access,
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    ticket_id: int | None = None,
    q: str | None = None,
    queue: str | None = None,
    sort: str = "updated",
    descending: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> TaskListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    filters = dict(status_id=status_id, dept_id=dept_id, assignee_staff_id=assignee_staff_id,
                   ticket_id=ticket_id, q=q, queue=queue)
    rows = await repo.list_tasks(session, access=access, **filters, sort=sort, descending=descending, limit=page_size, offset=offset)
    total = await repo.count_tasks(session, access=access, **filters)
    return TaskListResponse(
        items=[_row_to_list_item(dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


_QUEUE_TITLES = (("open", "Open"), ("overdue", "Overdue"), ("assigned", "My Tasks"), ("closed", "Completed"))   # osTicket's words


async def queue_counts(session: AsyncSession, *, access) -> TaskQueueCounts:
    counts = await repo.count_task_queues(session, access=access)
    return TaskQueueCounts(queues=[TaskQueueCount(key=key, title=title, count=counts[key]) for key, title in _QUEUE_TITLES])


async def task_assignees(session: AsyncSession, task_id: int, *, access):
    """As ``tickets.service.ticket_assignees``, for a task (``task.assign`` in the task's department)."""
    row = await repo.get_task(session, task_id, access=access)
    if row is None:
        return None
    if not access.can_role_perm_in("task.assign", int(row["dept_id"] or 0)):
        raise PermissionError("task.assign")
    return await agents_service.assignee_options(session, access=access, dept_id=int(row["dept_id"] or 0))


async def get_task(session: AsyncSession, task_id: int, *, access) -> TaskDetail | None:
    """The task with its thread, or None if it does not exist or ``access`` may not open it."""
    row = await repo.get_task(session, task_id, access=access)
    if row is None:
        return None
    thread_rows = await repo.get_task_thread(session, task_id, access=access)
    files = group_by_entry(await repo.get_task_attachments(session, task_id, access=access))
    custom_rows = await repo.get_task_custom_fields(session, task_id, access=access)
    collab_rows = await repo.get_task_collaborators(session, task_id, access=access)
    item = _row_to_list_item(row)
    return TaskDetail(
        **item.model_dump(),
        thread=[_row_to_thread_entry(dict(r), files.get(int(r['id']))) for r in thread_rows],
        collaborators=[_row_to_collaborator(dict(r)) for r in collab_rows],
        custom_fields=_custom_answers(custom_rows, "A"),
    )


async def list_collaborators(session: AsyncSession, task_id: int, *, access) -> CollaboratorList | None:
    """The task's collaborators, or None if the task does not exist or ``access`` may not open it."""
    if await repo.get_task(session, task_id, access=access) is None:
        return None
    rows = await repo.get_task_collaborators(session, task_id, access=access)
    return CollaboratorList(collaborators=[_row_to_collaborator(dict(r)) for r in rows])


# ── Writes ─────────────────────────────────────────────────────────────────────


async def create_task(
    session: AsyncSession,
    body: TaskCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.create",
        aggregate_type="task",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def assign_task(
    session: AsyncSession,
    task_id: int,
    body: TaskAssign,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.assign",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def claim_task(
    session: AsyncSession,
    task_id: int,
    body: TaskClaim,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.claim",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def transfer_task(
    session: AsyncSession,
    task_id: int,
    body: TaskTransfer,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.transfer",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def add_collaborator(
    session: AsyncSession,
    task_id: int,
    body: TicketCollaboratorAdd,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.collaborator.add",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def edit_entry(
    session: AsyncSession,
    task_id: int,
    entry_id: int,
    body: TicketEntryEdit,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.entry.edit",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, "entry_id": entry_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def resend_entry(
    session: AsyncSession,
    task_id: int,
    entry_id: int,
    body: TicketEntryResend,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.entry.resend",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, "entry_id": entry_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def reply_to_task(
    session: AsyncSession,
    task_id: int,
    body: TaskReply,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.reply",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def close_task(
    session: AsyncSession,
    task_id: int,
    body: TaskClose,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.close",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def reopen_task(
    session: AsyncSession,
    task_id: int,
    body: TaskReopen,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.reopen",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def add_note(
    session: AsyncSession,
    task_id: int,
    body: TaskNote,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.note",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump()},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def edit_task(
    session: AsyncSession,
    task_id: int,
    body: TaskEdit,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.edit",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def delete_task(
    session: AsyncSession,
    task_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="task.delete",
        aggregate_type="task",
        aggregate_id=str(task_id),
        payload={"task_id": task_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def task_attachment(session: AsyncSession, task_id: int, attachment_id: int, *, access) -> dict | None:
    """The attachment's file row if it is attached to this task's thread and the task may be opened, else None."""
    rows = await repo.get_task_attachments(session, task_id, access=access, attachment_id=attachment_id)
    return dict(rows[0]) if rows else None
