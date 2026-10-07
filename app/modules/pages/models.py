from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

PageType = Literal["landing", "offline", "thank-you", "other"]


class PageItem(BaseModel):
    id: int
    name: str
    type: PageType
    is_active: bool
    in_use: bool = Field(description="A default page (landing, offline, thank-you) or a help topic's: it cannot be disabled or deleted")
    created: Instant
    updated: Instant


class PageDetail(PageItem):
    body: str = ""
    notes: str = ""


class PageList(BaseModel):
    items: list[PageItem]
    total: int
    page: int
    page_size: int


class PageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: PageType
    body: str = Field(min_length=1, max_length=200000)
    is_active: bool = True
    notes: str = Field(default="", max_length=10000)


class PageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: Optional[PageType] = None
    body: Optional[str] = Field(default=None, min_length=1, max_length=200000)
    is_active: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=10000)
