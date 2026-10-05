"""Ticket domain Pydantic models.

Three tiers:
  - Request bodies  (TicketCreate, TicketReply, etc.)
  - Command response (CommandResponse) — returned by all write endpoints
  - Read responses  (TicketListItem, TicketDetail, ThreadEntry, etc.)
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.custom_fields import CustomFieldAnswer, CustomFields


# ── Shared response ────────────────────────────────────────────────────────────


class CommandResponse(BaseModel):
    """Returned by every write (mutating) ticket endpoint.

    The command has been queued in ``ost_integration_inbox``.  The PHP bridge
    will execute it on the next cron cycle (typically within 60 seconds) and
    emit an event to ``ost_integration_outbox``, which the Python outbox
    worker broadcasts over WebSocket.
    """

    command_id: int = Field(description="Row ID in ost_integration_inbox.")
    status: str = Field(
        description="Command status: 'pending' immediately after creation."
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Echo of the client-supplied Idempotency-Key header, if any.",
    )


# ── Read response sub-models ───────────────────────────────────────────────────


class TicketStatusSchema(BaseModel):
    id: int
    name: str
    state: str  # open | closed | archived | deleted


class TicketListItem(BaseModel):
    """Lightweight ticket row returned in list responses."""

    ticket_id: int
    number: str
    subject: str
    status: TicketStatusSchema
    dept_id: Optional[int] = None
    dept_name: Optional[str] = None
    assignee_staff_id: Optional[int] = None
    assignee_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    topic_id: Optional[int] = None
    topic_name: Optional[str] = None
    sla_id: Optional[int] = None
    sla_name: Optional[str] = None
    priority_id: Optional[int] = None
    priority_name: Optional[str] = None
    source: str = ""
    is_answered: bool = False
    created: datetime
    updated: datetime
    closed: Optional[datetime] = None
    due_date: Optional[datetime] = None
    est_due_date: Optional[datetime] = Field(
        default=None, description="osTicket's due date: the manual one, else the SLA's estimate (what its pages show)")
    is_overdue: bool = Field(default=False, description="osTicket's overdue flag (what the Overdue queue lists)")
    last_activity: Optional[datetime] = Field(
        default=None, description="osTicket's 'Last Updated' (lastupdate): moves on a message or a response")


class Attachment(BaseModel):
    id: int
    name: str
    size: int
    content_type: str
    inline: bool = False
    # download: GET /tickets/{id}/attachments/{attachment id} (tasks: /tasks/{id}/attachments/{attachment id})


class ThreadEntry(BaseModel):
    id: int
    type: str = Field(description="Entry type: M=message, R=reply, N=note, E=event")
    title: Optional[str] = None
    body: str
    format: Literal["text", "html"] = Field(default="text", description="How osTicket stores the body")
    staff_id: Optional[int] = None
    user_id: Optional[int] = None
    poster: Optional[str] = None
    ip_address: Optional[str] = None
    created: datetime
    is_edited: bool = False
    attachments: list[Attachment] = []


class Collaborator(BaseModel):
    id: int
    user_id: int
    name: str
    email: str
    is_active: bool = True
    is_cc: bool = True


class CollaboratorList(BaseModel):
    collaborators: list[Collaborator]


class Referral(BaseModel):
    id: int
    referee_type: Literal["agent", "team", "department"]
    referee_id: int
    name: str
    created: datetime


class ReferralList(BaseModel):
    referrals: list[Referral]


class TicketDetail(TicketListItem):
    """Full ticket including thread and collaborators."""

    thread: list[ThreadEntry] = []
    collaborators: list[Collaborator] = []
    custom_fields: list[CustomFieldAnswer] = []


TicketQueue = Literal["open", "unanswered", "answered", "overdue", "mine", "assigned_me", "assigned_teams", "closed"]
TicketSort = Literal["updated", "last_activity", "created", "due_date", "number", "subject", "status", "dept", "priority", "assignee", "requester"]


class QueueCount(BaseModel):
    key: TicketQueue
    title: str
    parent: Optional[TicketQueue] = None
    count: int


class QueueCounts(BaseModel):
    """osTicket's default queues in its own order (a child follows its parent), counted over the agent's list."""

    queues: list[QueueCount]


class TicketListResponse(BaseModel):
    items: list[TicketListItem]
    total: Optional[int] = None
    page: int
    page_size: int


# ── Write request bodies ───────────────────────────────────────────────────────


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    email: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    dept_id: Optional[int] = None
    topic_id: Optional[int] = None
    priority_id: Optional[int] = None
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom form fields, by variable name.")


class TicketReply(BaseModel):
    message: str = Field(min_length=1)
    signature: str = ""


class TicketNote(BaseModel):
    title: str = ""
    message: str = Field(min_length=1)


class TicketClose(BaseModel):
    note: str = ""


class TicketReopen(BaseModel):
    note: str = ""


class TicketAssign(BaseModel):
    staff_id: int = Field(gt=0)
    note: str = ""


class TicketAssignTeam(BaseModel):
    team_id: int = Field(gt=0)
    note: str = ""


class TicketTransfer(BaseModel):
    dept_id: int = Field(gt=0)
    note: str = ""


class TicketChangeOwner(BaseModel):
    user_id: int = Field(gt=0)


class TicketMerge(BaseModel):
    child_ids: list[int] = Field(min_length=1)


class TicketCollaboratorAdd(BaseModel):
    user_id: int = Field(gt=0)


class TicketUpdate(BaseModel):
    """Partial edit (osTicket's ticket edit page). Absent = leave as is; ``due_date: null`` clears it."""

    subject: Optional[str] = Field(default=None, min_length=1, max_length=255)
    priority_id: Optional[int] = Field(default=None, gt=0)
    topic_id: Optional[int] = Field(default=None, gt=0)
    sla_id: Optional[int] = Field(default=None, ge=0, description="0 = no SLA plan.")
    source: Optional[str] = Field(default=None, min_length=1, max_length=32)
    due_date: Optional[datetime] = Field(default=None, description="Must be in the future; null clears it.")
    note: Optional[str] = Field(default=None, max_length=10000, description="Logged as an internal note.")
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom form fields, by variable name.")


class TicketClaim(BaseModel):
    note: str = Field(default="", max_length=10000)


class TicketRelease(BaseModel):
    """Drop the agent and/or the team assignment (both = unassign)."""

    staff: bool = False
    team: bool = False
    note: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def _something_to_release(self):
        if not (self.staff or self.team):
            raise ValueError("set 'staff' and/or 'team' to say which assignment to release")
        return self


class TicketRefer(BaseModel):
    """Give an agent, team or department referral access; exactly one referee."""

    agent_id: Optional[int] = Field(default=None, gt=0)
    team_id: Optional[int] = Field(default=None, gt=0)
    dept_id: Optional[int] = Field(default=None, gt=0)
    note: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def _one_referee(self):
        if sum(v is not None for v in (self.agent_id, self.team_id, self.dept_id)) != 1:
            raise ValueError("give exactly one of agent_id, team_id, dept_id")
        return self


class TicketCollaboratorUpdate(BaseModel):
    """Absent = leave as is. ``is_active`` false stops the collaborator receiving the thread."""

    is_active: Optional[bool] = None
    is_cc: Optional[bool] = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.is_active is None and self.is_cc is None:
            raise ValueError("give is_active and/or is_cc")
        return self


class TicketEntryEdit(BaseModel):
    """Edit a thread entry. osTicket writes a new entry and hides the old one, so the id changes."""

    body: str = Field(min_length=1, max_length=100000)
    title: Optional[str] = Field(default=None, max_length=255)
    resend: bool = Field(default=False, description="Replies only: email the edited reply again.")
    signature: Literal["none", "mine", "theirs", "dept"] = "none"


class TicketEntryResend(BaseModel):
    signature: Literal["none", "mine", "theirs", "dept"] = "none"
