from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

Target = Literal["Any", "Web", "Email", "API"]
MatchType = Literal["equal", "not_equal", "contains", "dn_contain", "starts", "ends", "match", "not_match"]
ActionType = Literal["reject", "replyto", "noresp", "canned", "dept", "pri", "sla", "team", "agent", "topic", "status", "email"]

# osTicket's action types: label, the field(s) its configuration takes, whether several may be used
ACTION_TYPES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "reject": ("Reject Ticket", (), False),
    "replyto": ("Use Reply-To Email", (), False),
    "noresp": ("Disable autoresponse", (), False),
    "canned": ("Attach Canned Response", ("canned_id",), False),
    "dept": ("Set Department", ("dept_id",), False),
    "pri": ("Set Priority", ("priority_id",), False),
    "sla": ("Set SLA Plan", ("sla_id",), False),
    "team": ("Assign to Team", ("team_id",), False),
    "agent": ("Assign to Agent", ("staff_id",), False),
    "topic": ("Set Help Topic", ("topic_id",), False),
    "status": ("Set Ticket Status", ("status_id",), False),
    "email": ("Send an Email", ("recipients", "subject", "message", "from_email_id"), True),
}


class FilterRule(BaseModel):
    what: str = Field(min_length=1, max_length=32, description="The field matched; see GET /filters/options")
    how: MatchType
    val: str = Field(min_length=1, max_length=255)


class FilterRuleItem(FilterRule):
    id: int


class FilterAction(BaseModel):
    """One thing a filter does. Which fields apply depends on ``type`` (``ACTION_TYPES``)."""

    type: ActionType
    canned_id: Optional[int] = Field(default=None, gt=0)
    dept_id: Optional[int] = Field(default=None, gt=0)
    priority_id: Optional[int] = Field(default=None, gt=0)
    sla_id: Optional[int] = Field(default=None, gt=0)
    team_id: Optional[int] = Field(default=None, gt=0)
    staff_id: Optional[int] = Field(default=None, gt=0)
    topic_id: Optional[int] = Field(default=None, gt=0)
    status_id: Optional[int] = Field(default=None, gt=0)
    recipients: Optional[str] = Field(default=None, max_length=1000, description="Send an Email: addresses, comma separated; %{user} is the requester")
    subject: Optional[str] = Field(default=None, max_length=255)
    message: Optional[str] = Field(default=None, max_length=100000)
    from_email_id: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _the_fields_the_type_needs(self):
        needed = ACTION_TYPES[self.type][1]
        optional = {"from_email_id"}
        missing = [f for f in needed if f not in optional and getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"a '{self.type}' action needs {', '.join(missing)}")
        stray = [f for f in ("canned_id", "dept_id", "priority_id", "sla_id", "team_id", "staff_id", "topic_id", "status_id",
                             "recipients", "subject", "message", "from_email_id")
                 if f not in needed and getattr(self, f) is not None]
        if stray:
            raise ValueError(f"a '{self.type}' action does not take {', '.join(stray)}")
        return self


class FilterActionItem(FilterAction):
    id: int
    sort: int


class FilterItem(BaseModel):
    id: int
    name: str
    execorder: int
    is_active: bool
    target: Target
    email_id: Optional[int] = Field(default=None, description="For target Email: only mail to this address (0/absent = all)")
    email_address: Optional[str] = None
    match_all_rules: bool
    stop_onmatch: bool
    rule_count: int
    created: Instant
    updated: Instant


class FilterDetail(FilterItem):
    notes: str = ""
    rules: list[FilterRuleItem]
    actions: list[FilterActionItem]


class FilterList(BaseModel):
    items: list[FilterItem]
    total: int
    page: int
    page_size: int


class FilterBase(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    execorder: int = Field(default=99, ge=1, le=2147483647, description="Lower runs first")
    is_active: bool = True
    target: Target = "Any"
    email_id: Optional[int] = Field(default=None, ge=0, description="With target Email: match only mail to this system address")
    match_all_rules: bool = Field(default=False, description="All rules must match (default: any one)")
    stop_onmatch: bool = False
    notes: str = Field(default="", max_length=10000)


class FilterCreate(FilterBase):
    rules: list[FilterRule] = Field(min_length=1, max_length=50)
    actions: list[FilterAction] = Field(default=[], max_length=20)


class FilterUpdate(BaseModel):
    """Partial. ``rules`` and ``actions``, when sent, replace the whole list."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    execorder: Optional[int] = Field(default=None, ge=1, le=2147483647)
    is_active: Optional[bool] = None
    target: Optional[Target] = None
    email_id: Optional[int] = Field(default=None, ge=0)
    match_all_rules: Optional[bool] = None
    stop_onmatch: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=10000)
    rules: Optional[list[FilterRule]] = Field(default=None, min_length=1, max_length=50)
    actions: Optional[list[FilterAction]] = Field(default=None, max_length=20)


class MatchField(BaseModel):
    key: str
    label: str


class MatchGroup(BaseModel):
    group: str
    fields: list[MatchField]


class ActionTypeInfo(BaseModel):
    type: ActionType
    label: str
    fields: list[str]
    multi_use: bool


class FilterOptions(BaseModel):
    """What a filter form offers: the fields rules can match (osTicket builds them from the ticket forms), the match
    types, the targets and the action types."""

    matches: list[MatchGroup]
    match_types: list[MatchField]
    targets: list[str]
    actions: list[ActionTypeInfo]
