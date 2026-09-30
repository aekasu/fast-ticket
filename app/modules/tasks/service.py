"""Task service layer."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.tasks.models import (
    TaskAssign,
    TaskClose,
    TaskCreate,
    TaskDetail,
    TaskEdit,
    TaskListItem,
    TaskListResponse,
    TaskNote,
    TaskReopen,
    TaskStatusSchema,
)
from app.modules.tickets.models import CommandResponse, ThreadEntry

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
        created=row["created"],
        updated=row["updated"],
        closed=row.get("closed"),
        due_date=row.get("due_date"),
    )


def _row_to_thread_entry(row: dict) -> ThreadEntry:
    return ThreadEntry(
        id=row["id"],
        type=row.get("type") or "N",
        title=row.get("title"),
        body=row.get("body") or "",
        staff_id=row.get("staff_id") or None,
        user_id=row.get("user_id") or None,
        poster=row.get("poster"),
        ip_address=row.get("ip_address"),
        created=row["created"],
    )


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
    page: int = 1,
    page_size: int = 50,
) -> TaskListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_tasks(
        session,
        access=access,
        status_id=status_id,
        dept_id=dept_id,
        assignee_staff_id=assignee_staff_id,
        ticket_id=ticket_id,
        limit=page_size,
        offset=offset,
    )
    return TaskListResponse(
        items=[_row_to_list_item(dict(r)) for r in rows],
        page=page,
        page_size=page_size,
    )


async def get_task(session: AsyncSession, task_id: int, *, access) -> TaskDetail | None:
    """The task with its thread, or None if it does not exist or ``access`` may not open it."""
    row = await repo.get_task(session, task_id, access=access)
    if row is None:
        return None
    thread_rows = await repo.get_task_thread(session, task_id, access=access)
    item = _row_to_list_item(row)
    return TaskDetail(
        **item.model_dump(),
        thread=[_row_to_thread_entry(dict(r)) for r in thread_rows],
    )


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
