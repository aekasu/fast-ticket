"""Task endpoints.

Permission map (role permissions; reads use ``task.create`` as a stand-in until read scoping, S3):
  task.create  — create tasks
  task.close   — close / reopen tasks
  task.assign  — assign tasks to staff
  task.delete  — delete tasks
  task.edit    — edit task metadata
  task.reply   — add notes to tasks
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.tasks import service
from app.modules.tasks.models import (
    TaskAssign,
    TaskClose,
    TaskCreate,
    TaskDetail,
    TaskEdit,
    TaskListResponse,
    TaskNote,
    TaskReopen,
)
from app.modules.tickets.models import CommandResponse

router = APIRouter(tags=["tasks"])


# ── Reads ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=TaskListResponse, summary="List tasks")
async def list_tasks(
    status_id: int | None = Query(default=None, description="Filter by status ID"),
    dept_id: int | None = Query(default=None, description="Filter by department ID"),
    assignee_staff_id: int | None = Query(default=None, description="Filter by assigned staff"),
    ticket_id: int | None = Query(default=None, description="Filter to tasks linked to a ticket"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("task.create"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_tasks(
        session,
        status_id=status_id,
        dept_id=dept_id,
        assignee_staff_id=assignee_staff_id,
        ticket_id=ticket_id,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}", response_model=TaskDetail, summary="Get a task with its thread")
async def get_task(
    task_id: int,
    current_user: dict = require_perm("task.create"),
    session: AsyncSession = Depends(get_session),
):
    task = await service.get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
    return task


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
    summary="Assign a task to a staff member",
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
