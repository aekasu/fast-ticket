from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

SortMode = Literal["Alpha", "-Alpha", "SortCol"]


class CustomListItem(BaseModel):
    id: int
    name: str
    name_plural: str = ""
    sort_mode: SortMode
    notes: str = ""
    item_count: int
    handled_by: Optional[str] = Field(
        default=None, description="Set for a list osTicket manages itself (ticket statuses): it is read-only here")
    is_editable: bool
    allow_add: bool
    is_deletable: bool
    created: Instant
    updated: Instant


class CustomListResponse(BaseModel):
    items: list[CustomListItem]
    total: int
    page: int
    page_size: int


class ListEntry(BaseModel):
    id: int
    value: str
    abbrev: str = ""
    sort: int
    is_enabled: bool
    is_internal: bool = Field(description="Internal entries cannot be disabled or deleted")


class ListEntryResponse(BaseModel):
    items: list[ListEntry]
    total: int
    page: int
    page_size: int


class ListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    name_plural: str = Field(default="", max_length=255)
    sort_mode: SortMode = "Alpha"
    notes: str = Field(default="", max_length=10000)


class ListUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    name_plural: Optional[str] = Field(default=None, max_length=255)
    sort_mode: Optional[SortMode] = None
    notes: Optional[str] = Field(default=None, max_length=10000)


class EntryCreate(BaseModel):
    value: str = Field(min_length=1, max_length=255)
    abbrev: str = Field(default="", max_length=255)
    sort: int = Field(default=1, ge=0, le=2147483647)
    is_enabled: bool = True


class EntryUpdate(BaseModel):
    value: Optional[str] = Field(default=None, min_length=1, max_length=255)
    abbrev: Optional[str] = Field(default=None, max_length=255)
    sort: Optional[int] = Field(default=None, ge=0, le=2147483647)
    is_enabled: Optional[bool] = None
