"""Task domain Pydantic models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.custom_fields import CustomFieldAnswer, CustomFields

from app.modules.tickets.models import (
    Collaborator,
    CollaboratorList,
    CommandResponse,
    ThreadEntry,
    TicketCollaboratorAdd,
    TicketEntryEdit,
    TicketEntryResend,
)  # reuse shared types

__all__ = ["CommandResponse", "ThreadEntry"]  # re-export for convenience


# ── Read response models ───────────────────────────────────────────────────────


class TaskStatusSchema(BaseModel):
    id: int
    name: str
    state: str  # open | closed


TaskSort = Literal["updated", "created", "due_date", "number", "title", "dept"]


class TaskListItem(BaseModel):
    task_id: int
    number: str
    title: str
    status: TaskStatusSchema
    dept_id: Optional[int] = None
    dept_name: Optional[str] = None
    assignee_staff_id: Optional[int] = None
    assignee_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    ticket_id: Optional[int] = None  # parent ticket, if linked
    created: datetime
    updated: datetime
    closed: Optional[datetime] = None
    due_date: Optional[datetime] = None


class TaskDetail(TaskListItem):
    thread: list[ThreadEntry] = []
    collaborators: list[Collaborator] = []
    custom_fields: list[CustomFieldAnswer] = []


class TaskListResponse(BaseModel):
    items: list[TaskListItem]
    total: Optional[int] = None
    page: int
    page_size: int


# ── Write request bodies ───────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    # osTicket's default task form requires a description; without one the bridge could only
    # answer "Description is a required field" after the command had been queued.
    description: str = Field(min_length=1)
    dept_id: Optional[int] = None
    ticket_id: Optional[int] = Field(
        default=None,
        description="Link this task to a ticket. Omit for a standalone task.",
    )
    due_date: Optional[datetime] = None
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom task-form fields, by variable name.")


class TaskAssign(BaseModel):
    """Assign to an agent or to a team — exactly one."""

    staff_id: Optional[int] = Field(default=None, gt=0)
    team_id: Optional[int] = Field(default=None, gt=0)
    note: str = ""

    @model_validator(mode="after")
    def _one_assignee(self):
        if (self.staff_id is None) == (self.team_id is None):
            raise ValueError("give exactly one of staff_id, team_id")
        return self


class TaskClaim(BaseModel):
    note: str = Field(default="", max_length=10000)


class TaskTransfer(BaseModel):
    dept_id: int = Field(gt=0)
    note: str = Field(default="", max_length=10000)


class TaskReply(BaseModel):
    """An update posted to the task's collaborators."""

    message: str = Field(min_length=1, max_length=100000)


class TaskClose(BaseModel):
    note: str = ""


class TaskReopen(BaseModel):
    note: str = ""


class TaskNote(BaseModel):
    title: str = ""
    note: str = Field(min_length=1)


class TaskEdit(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    dept_id: Optional[int] = None
    due_date: Optional[datetime] = None
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom task-form fields, by variable name.")
    note: Optional[str] = Field(default=None, min_length=1, max_length=10000, description="Reason for editing; posted as an internal note, as osTicket's edit form does.")
