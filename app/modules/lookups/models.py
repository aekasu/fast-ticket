"""Lookups (Topics, SLAs, Priorities) domain Pydantic models."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


# ── Help Topics ───────────────────────────────────────────────────────────────

TopicStatus = Literal["active", "disabled", "archived"]

class TopicListItem(BaseModel):
    id: int
    topic: str
    is_active: bool
    is_public: bool
    created: Instant
    updated: Instant
    path: Optional[str] = Field(default=None, description="Parent / Child, as osTicket's Help Topics page shows it (admin list)")
    priority_name: Optional[str] = None
    dept_name: Optional[str] = None

class TopicListResponse(BaseModel):
    items: list[TopicListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class TopicDetail(TopicListItem):
    parent_id: int = 0
    dept_id: int = 0
    priority_id: int = 0
    status_id: int = Field(default=0, description="Default ticket status (0 = osTicket's default).")
    sla_id: int = 0
    page_id: int = 0
    status: TopicStatus = "active"
    notes: str = ""
    no_auto_response: bool = False
    custom_numbers: bool = False
    sequence_id: int = 0
    number_format: str = ""
    assign_staff_id: int = Field(default=0, description="Auto-assign new tickets to this agent (0 = none).")
    assign_team_id: int = Field(default=0, description="Auto-assign new tickets to this team (0 = none).")
    form_ids: list[int] = Field(default=[], description="Forms attached, in order (the ticket form included).")


class _TopicFields(BaseModel):
    """Everything osTicket's help-topic page edits. ``0`` for an id means none."""

    status: Optional[TopicStatus] = None
    is_public: Optional[bool] = None
    parent_id: Optional[int] = Field(default=None, ge=0)
    dept_id: Optional[int] = Field(default=None, ge=0)
    priority_id: Optional[int] = Field(default=None, ge=0)
    status_id: Optional[int] = Field(default=None, ge=0)
    sla_id: Optional[int] = Field(default=None, ge=0)
    page_id: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=10000)
    no_auto_response: Optional[bool] = None
    custom_numbers: Optional[bool] = Field(default=None, description="Use this topic's own ticket-number format.")
    sequence_id: Optional[int] = Field(default=None, ge=0)
    number_format: Optional[str] = Field(default=None, max_length=32, description="Must contain a # when custom_numbers is on.")
    assign_staff_id: Optional[int] = Field(default=None, ge=0)
    assign_team_id: Optional[int] = Field(default=None, ge=0)
    form_ids: Optional[list[int]] = Field(
        default=None, description="Forms to attach, in order; replaces the list (the ticket form always stays).")

    @model_validator(mode="after")
    def _one_auto_assignee(self):
        if self.assign_staff_id and self.assign_team_id:
            raise ValueError("auto-assign to an agent or a team, not both")
        return self

    @field_validator("form_ids")
    @classmethod
    def _forms_once(cls, value):
        if value is not None and (len(set(value)) != len(value) or any(i <= 0 for i in value)):
            raise ValueError("form ids must be positive and listed once")
        return value


class TopicCreate(_TopicFields):
    topic: str = Field(min_length=1, max_length=255)


class TopicUpdate(_TopicFields):
    topic: Optional[str] = Field(default=None, min_length=1, max_length=255)


# ── SLAs (Service Level Agreements) ───────────────────────────────────────────

class SLAListItem(BaseModel):
    id: int
    name: str
    grace_period: int  # Hours
    is_active: bool
    created: Instant
    updated: Instant

class SLAListResponse(BaseModel):
    items: list[SLAListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class SLADetail(SLAListItem):
    schedule_id: int = Field(default=0, description="Business-hours schedule (0 = 24/7).")
    transient: bool = Field(default=False, description="A transient plan is replaced by the next ticket-routing SLA.")
    disable_overdue_alerts: bool = False
    notes: str = ""

class SLACreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grace_period: int = Field(ge=0, le=8760, description="Hours.")
    is_active: bool = True
    schedule_id: int = Field(default=0, ge=0)
    transient: bool = False
    disable_overdue_alerts: bool = False
    notes: str = Field(default="", max_length=10000)

class SLAUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    grace_period: Optional[int] = Field(default=None, ge=0, le=8760)
    is_active: Optional[bool] = None
    schedule_id: Optional[int] = Field(default=None, ge=0)
    transient: Optional[bool] = None
    disable_overdue_alerts: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=10000)


# ── Priorities ────────────────────────────────────────────────────────────────

class PriorityListItem(BaseModel):
    id: int
    name: str
    desc: str
    urgency: int
    is_public: bool

class TicketStatusItem(BaseModel):
    id: int
    name: str
    state: str = Field(description="open | closed | archived | deleted; the New Ticket form offers the open ones")


TicketState = Literal["open", "closed", "archived", "deleted"]


class TicketStatusDetail(TicketStatusItem):
    """A ticket status as the Admin Panel's Ticket Statuses page holds it."""

    description: str = ""
    sort: int = 0
    is_enabled: bool
    is_internal: bool = Field(description="A built-in status or the system default: its name and state are fixed, and it cannot be disabled or deleted")
    is_default: bool = Field(description="The status new tickets start in (Settings > Tickets)")
    allow_reopen: bool = Field(default=False, description="A closed status: end users may reopen their ticket")
    reopen_status_id: Optional[int] = Field(default=None, description="The open status a reopened ticket gets; none = the system default")
    ticket_count: int = 0
    is_deletable: bool = Field(description="Not internal and no ticket has it")


class TicketStatusAdminList(BaseModel):
    items: list[TicketStatusDetail]


class TicketStatusCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    state: TicketState
    description: str = Field(default="", max_length=100)
    sort: Optional[int] = Field(default=None, ge=0, le=65535, description="Default: after the others")
    allow_reopen: bool = False
    reopen_status_id: Optional[int] = Field(default=None, ge=0)
    is_enabled: bool = True


class TicketStatusUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    state: Optional[TicketState] = None
    description: Optional[str] = Field(default=None, max_length=100)
    sort: Optional[int] = Field(default=None, ge=0, le=65535)
    allow_reopen: Optional[bool] = None
    reopen_status_id: Optional[int] = Field(default=None, ge=0)
    is_enabled: Optional[bool] = None


class TicketStatusListResponse(BaseModel):
    statuses: list[TicketStatusItem]


class PriorityListResponse(BaseModel):
    items: list[PriorityListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class PriorityDetail(PriorityListItem):
    pass

class PriorityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    desc: str = ""
    urgency: int = 0
    is_public: bool = True

class PriorityUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    desc: Optional[str] = None
    urgency: Optional[int] = None
    is_public: Optional[bool] = None
