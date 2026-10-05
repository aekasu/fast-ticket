"""Banlist domain Pydantic models."""

from typing import Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

class BanlistListItem(BaseModel):
    id: int
    val: str  # The email or domain
    is_active: bool
    created: Instant
    updated: Instant

class BanlistListResponse(BaseModel):
    items: list[BanlistListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class BanlistDetail(BanlistListItem):
    notes: str = ""

class BanlistCreate(BaseModel):
    val: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    notes: str = ""

class BanlistUpdate(BaseModel):
    val: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_active: Optional[bool] = None
    notes: Optional[str] = None
