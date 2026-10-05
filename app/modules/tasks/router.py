"""Task endpoints.

Reads need no permission: which tasks an agent sees is osTicket's own visibility rule, applied in the
query (``read_repository.task_visibility``); a task they may not open is a 404.

Writes — permission map (role permissions):
  task.create  — create tasks
  task.close   — close / reopen tasks
  task.assign  — assign tasks to an agent or team, claim
  task.transfer — transfer to another department
  task.delete  — delete tasks
  task.edit    — edit task metadata
  ticket.edit  — add a collaborator (osTicket's task page checks the ticket permission)
  task.reply   — add notes and post updates; edit and resend thread entries (the bridge applies osTicket's exact edit rule)
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachments import file_response
from app.core.db import get_session
from app.core.security import require_perm, require_staff
from app.modules.agents.models import AssigneeOptions
from app.modules.tasks import service
from app.modules.tasks.models import (
    TaskSort,
    TaskAssign,
    TaskClaim,
    TaskClose,
    TaskCreate,
    TaskDetail,
    TaskEdit,
    TaskListResponse,
    TaskQueue,
    TaskQueueCounts,
    TaskNote,
    TaskReopen,
    TaskReply,
    TaskTransfer,
)
from app.modules.tickets.models import CollaboratorList, CommandResponse, TicketCollaboratorAdd, TicketEntryEdit, TicketEntryResend

router = APIRouter(tags=["tasks"])


# ── Reads ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=TaskListResponse, summary="List tasks")
async def list_tasks(
    status_id: int | None = Query(default=None, description="Filter by status ID"),
    dept_id: int | None = Query(default=None, description="Filter by department ID"),
    assignee_staff_id: int | None = Query(default=None, description="Filter by assigned staff"),
    ticket_id: int | None = Query(default=None, description="Filter to tasks linked to a ticket"),
    q: str | None = Query(default=None, max_length=100, description="Task number or title (contains)"),
    queue: TaskQueue | None = Query(default=None, description="One of osTicket's task queues (see GET /tasks/queues)"),
    sort: TaskSort = Query(default="updated"),
    descending: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """The tasks the agent may see: open and assigned to them or their team, tasks of their tickets,
    and their departments' (unless limited to assigned-only) — as osTicket's task list."""
    return await service.list_tasks(
        session,
        access=current_user["access"],
        status_id=status_id,
        dept_id=dept_id,
        assignee_staff_id=assignee_staff_id,
        ticket_id=ticket_id,
        q=q,
        queue=queue,
        sort=sort,
        descending=descending,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{task_id}/attachments/{attachment_id}",
    summary="Download a file attached to one of the task's thread entries",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_task_attachment(
    task_id: int,
    attachment_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    row = await service.task_attachment(session, task_id, attachment_id, access=current_user["access"])
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return await file_response(row, staff_id=current_user["staff_id"], object_type="A", object_id=task_id)


@router.get("/{task_id}/assignees", response_model=AssigneeOptions, summary="Agents and teams this task can be assigned to")
async def task_assignees(
    task_id: int,
    current_user: dict = require_perm("task.assign"),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await service.task_assignees(session, task_id, access=current_user["access"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied: task.assign is required in the task's department.")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
    return result


@router.get("/queues", response_model=TaskQueueCounts, summary="Task counts for osTicket's queues")
async def task_queues(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """Open, Overdue, My Tasks (open and assigned to the agent) and Completed — each counted among the tasks the
    agent's own list shows."""
    return await service.queue_counts(session, access=current_user["access"])


@router.get("/{task_id}", response_model=TaskDetail, summary="Get a task with its thread")
async def get_task(
    task_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    task = await service.get_task(session, task_id, access=current_user["access"])
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
    return task


@router.get("/{task_id}/collaborators", response_model=CollaboratorList, summary="List a task's collaborators")
async def list_collaborators(
    task_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    result = await service.list_collaborators(session, task_id, access=current_user["access"])
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
    return result


# ── Writes ─────────────────────────────────────────────────────────────────────


@router.post("/", response_model=CommandResponse, status_code=202, summary="Create a task")
async def create_task(
    body: TaskCreate,
    current_user: dict = require_perm("task.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Create a standalone task or link it to a ticket via ``ticket_id``."""
    return await service.create_task(
        session, body, current_user["staff_id"], idempotency_key
    )


@router.patch(
    "/{task_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Edit task metadata (title, description, dept, due date)",
)
async def edit_task(
    task_id: int,
    body: TaskEdit,
    current_user: dict = require_perm("task.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.edit_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/assign",
    response_model=CommandResponse,
    status_code=202,
    summary="Assign a task to an agent or a team",
)
async def assign_task(
    task_id: int,
    body: TaskAssign,
    current_user: dict = require_perm("task.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.assign_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/claim",
    response_model=CommandResponse,
    status_code=202,
    summary="Assign an open task to yourself",
)
async def claim_task(
    task_id: int,
    body: TaskClaim,
    current_user: dict = require_perm("task.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.claim_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/transfer",
    response_model=CommandResponse,
    status_code=202,
    summary="Transfer a task to another department",
)
async def transfer_task(
    task_id: int,
    body: TaskTransfer,
    current_user: dict = require_perm("task.transfer"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.transfer_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/reply",
    response_model=CommandResponse,
    status_code=202,
    summary="Post an update to the task's collaborators",
)
async def reply_to_task(
    task_id: int,
    body: TaskReply,
    current_user: dict = require_perm("task.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.reply_to_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/collaborators",
    response_model=CommandResponse,
    status_code=202,
    summary="Add a collaborator to a task",
)
async def add_collaborator(
    task_id: int,
    body: TicketCollaboratorAdd,
    # osTicket's task page (scp/tasks.php "addcc") checks the *ticket* edit permission in the department role
    current_user: dict = require_perm("ticket.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_collaborator(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.patch(
    "/{task_id}/thread/{entry_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Edit a thread entry (the entry gets a new id; the old one is hidden)",
)
async def edit_entry(
    task_id: int,
    entry_id: int,
    body: TicketEntryEdit,
    current_user: dict = require_perm("task.reply"),   # gate; the bridge applies osTicket's exact edit rule
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.edit_entry(
        session, task_id, entry_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/thread/{entry_id}/resend",
    response_model=CommandResponse,
    status_code=202,
    summary="Resend a staff reply to the task's collaborators",
)
async def resend_entry(
    task_id: int,
    entry_id: int,
    body: TicketEntryResend,
    current_user: dict = require_perm("task.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.resend_entry(
        session, task_id, entry_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/close",
    response_model=CommandResponse,
    status_code=202,
    summary="Close / complete a task",
)
async def close_task(
    task_id: int,
    body: TaskClose = TaskClose(),
    current_user: dict = require_perm("task.close"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.close_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/reopen",
    response_model=CommandResponse,
    status_code=202,
    summary="Re-open a closed task",
)
async def reopen_task(
    task_id: int,
    body: TaskReopen = TaskReopen(),
    current_user: dict = require_perm("task.close"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.reopen_task(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.post(
    "/{task_id}/note",
    response_model=CommandResponse,
    status_code=202,
    summary="Add a note to a task",
)
async def add_note(
    task_id: int,
    body: TaskNote,
    current_user: dict = require_perm("task.reply"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.add_note(
        session, task_id, body, current_user["staff_id"], idempotency_key
    )


@router.delete(
    "/{task_id}",
    response_model=CommandResponse,
    status_code=202,
    summary="Delete a task",
)
async def delete_task(
    task_id: int,
    current_user: dict = require_perm("task.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_task(
        session, task_id, current_user["staff_id"], idempotency_key
    )
