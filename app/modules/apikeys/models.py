from typing import Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


class ApiKey(BaseModel):
    """osTicket's own API keys (Admin Panel → Manage → API Keys) — the ones its HTTP API and email piping check, not
    this API's login. The key itself is shown to administrators, as osTicket's page does."""

    id: int
    key: str
    ip_address: str
    is_active: bool
    can_create_tickets: bool
    can_exec_cron: bool
    notes: str = ""
    created: Instant
    updated: Instant


class ApiKeyList(BaseModel):
    items: list[ApiKey]
    total: int
    page: int
    page_size: int


class ApiKeyCreate(BaseModel):
    ip_address: str = Field(min_length=3, max_length=64, description="The only address osTicket will accept the key from")
    is_active: bool = True
    can_create_tickets: bool = True
    can_exec_cron: bool = False
    notes: str = Field(default="", max_length=2000)


class ApiKeyUpdate(BaseModel):
    """The address cannot change after the key is made (osTicket's page makes it read-only)."""

    is_active: Optional[bool] = None
    can_create_tickets: Optional[bool] = None
    can_exec_cron: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
