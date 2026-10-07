from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


class EmailAccountInfo(BaseModel):
    """A mail account of an address (fetching mail, or sending through SMTP). Never carries credentials."""

    id: int
    type: Literal["mailbox", "smtp"]
    is_active: bool
    host: str
    port: int
    protocol: str
    encryption: str
    folder: Optional[str] = None
    fetch_frequency: Optional[int] = Field(default=None, description="Minutes between fetches (mailbox)")
    fetch_max: Optional[int] = Field(default=None, description="Most messages per fetch (mailbox)")
    after_fetch: Optional[str] = Field(default=None, description="archive | delete | nothing (mailbox)")
    num_errors: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[Instant] = None
    last_activity: Optional[Instant] = None


class EmailAddress(BaseModel):
    id: int
    email: str
    name: str
    dept_id: Optional[int] = None
    dept_name: Optional[str] = None
    priority_id: Optional[int] = None
    priority_name: Optional[str] = None
    topic_id: Optional[int] = None
    topic_name: Optional[str] = None
    no_autoresponse: bool
    is_default: bool = Field(description="The system's default or alert address: it cannot be deleted")
    has_mailbox: bool
    has_smtp: bool
    created: Instant
    updated: Instant


class EmailAddressDetail(EmailAddress):
    notes: str = ""
    accounts: list[EmailAccountInfo] = []


class EmailAddressList(BaseModel):
    items: list[EmailAddress]
    total: int
    page: int
    page_size: int


class EmailCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(min_length=1, max_length=255)
    dept_id: int = Field(default=0, ge=0, description="0 = the system default department")
    priority_id: int = Field(default=0, ge=0, description="0 = the system default priority")
    topic_id: int = Field(default=0, ge=0, description="0 = the system default help topic")
    no_autoresponse: bool = False
    notes: str = Field(default="", max_length=10000)


class EmailUpdate(BaseModel):
    email: Optional[str] = Field(default=None, min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    dept_id: Optional[int] = Field(default=None, ge=0)
    priority_id: Optional[int] = Field(default=None, ge=0)
    topic_id: Optional[int] = Field(default=None, ge=0)
    no_autoresponse: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=10000)
