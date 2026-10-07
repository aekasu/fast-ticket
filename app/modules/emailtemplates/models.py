from typing import Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


class TemplateSet(BaseModel):
    id: int
    name: str
    lang: str = "en_US"
    notes: str = ""
    is_active: bool
    in_use: bool = Field(description="The default set, or a department's: it cannot be disabled or deleted")
    message_count: int
    created: Instant
    updated: Instant


class TemplateSetList(BaseModel):
    items: list[TemplateSet]
    total: int
    page: int
    page_size: int


class MessageTemplate(BaseModel):
    """One message of a set (osTicket's ``code_name``)."""

    code_name: str
    name: str = Field(description="osTicket's title for the message, e.g. New Ticket Auto-response")
    group: str = Field(description="osTicket's group: a.ticket.user, b.ticket.staff, c.task, sys")
    description: str = ""
    defined: bool = Field(description="False: the set has no copy yet; osTicket falls back to its built-in text until one is saved")
    subject: str = ""
    updated: Optional[Instant] = None


class TemplateSetDetail(TemplateSet):
    messages: list[MessageTemplate]


class MessageBody(MessageTemplate):
    body: str = ""


class TemplateSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    notes: str = Field(default="", max_length=10000)
    is_active: bool = True
    lang: Optional[str] = Field(default=None, max_length=16)
    copy_from_id: Optional[int] = Field(default=None, gt=0, description="Start from a copy of this set's messages")


class TemplateSetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_active: Optional[bool] = None
    lang: Optional[str] = Field(default=None, max_length=16)


class MessageUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=200000)
